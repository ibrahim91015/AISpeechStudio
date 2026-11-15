"""Web Push subscription validation and background delivery."""
import base64
import binascii
import logging
import os
import re
from tempfile import NamedTemporaryFile
from time import time
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid
from pywebpush import WebPushException, webpush
from pydantic import BaseModel, Field

from storage import DATA, connect


log = logging.getLogger(__name__)
MAX_ENDPOINT_LENGTH = 2048
PUSH_HOSTS = {"fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com",
              "push.services.mozilla.com", "notify.windows.com", "web.push.apple.com"}
PUSH_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".notify.live.net")


class Endpoint(BaseModel):
    endpoint: str = Field(max_length=MAX_ENDPOINT_LENGTH)


class Keys(BaseModel):
    p256dh: str = Field(max_length=100)
    auth: str = Field(max_length=32)


class Subscription(Endpoint):
    keys: Keys
    mobile: bool = False


def valid_endpoint(endpoint):
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        return (url.scheme == "https" and url.port in (None, 443) and url.path.startswith("/")
                and len(url.path) > 1 and not url.fragment and not url.username and not url.password
                and (host in PUSH_HOSTS or host.endswith(PUSH_SUFFIXES))
                and not re.search(r"[\s\\]", endpoint))
    except ValueError:
        return False


def valid_key(value, size):
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        return False
    try:
        raw = base64.b64decode(value.rstrip("=") + "==="[:(-len(value.rstrip("="))) % 4],
                               altchars=b"-_", validate=True)
        if len(raw) != size:
            return False
        if size == 65:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        return True
    except (binascii.Error, ValueError):
        return False


class PushSession(requests.Session):
    def request(self, method, url, **kwargs):
        # A push provider can expire an endpoint. It cannot redirect this server elsewhere.
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


class PushDelivery:
    def __init__(self):
        path = DATA / "push-vapid.pem"
        if not path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            with NamedTemporaryFile(dir=DATA) as staged:
                staged.write(pem)
                staged.flush()
                try:
                    os.link(staged.name, path)
                except FileExistsError:
                    pass
        self.vapid = Vapid.from_file(str(path))
        encoded = self.vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.public_key = base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")
        self.subject = os.getenv("TTS_PUSH_SUBJECT", "https://speech.example.com")

    def dispatch(self):
        now = int(time())
        with connect() as db:
            db.execute("DELETE FROM push_outbox WHERE expires<=?", (now,))
            db.execute("DELETE FROM page_notices WHERE expires<=?", (now,))
            due = db.execute("""
                SELECT o.*, s.p256dh, s.auth FROM push_outbox o JOIN push_subscriptions s USING(endpoint)
                WHERE next_attempt<=? ORDER BY next_attempt,id LIMIT 1
                """, (now,)).fetchone()
        if not due:
            return False
        if not valid_endpoint(due["endpoint"]):
            with connect() as db:
                db.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (due["endpoint"],))
            return True
        code = None
        try:
            with PushSession() as session:
                response = webpush(
                    {"endpoint": due["endpoint"], "keys": {"p256dh": due["p256dh"], "auth": due["auth"]}},
                    data=due["payload"], vapid_private_key=self.vapid, vapid_claims={"sub": self.subject},
                    ttl=max(1, due["expires"] - now), timeout=7, headers={"Urgency": "normal"}, requests_session=session,
                )
                code = response.status_code
        except WebPushException as exc:
            if exc.response is not None:
                code = exc.response.status_code
        except (requests.RequestException, ValueError):
            pass
        with connect() as db:
            if code in {404, 410}:
                db.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND auth=?",
                           (due["endpoint"], due["auth"]))
            elif code is not None and (200 <= code < 300 or code not in {408, 429} and code < 500):
                db.execute("DELETE FROM push_outbox WHERE id=?", (due["id"],))
                if not 200 <= code < 300:
                    log.warning("Push provider returned HTTP %s for delivery %s", code, due["id"])
            else:
                delay = min(300, 5 * (2 ** min(due["attempts"], 6)))
                db.execute("UPDATE push_outbox SET attempts=attempts+1,next_attempt=? WHERE id=?",
                           (int(time()) + delay, due["id"]))
        return True
