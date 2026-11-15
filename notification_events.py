"""Create durable notification deliveries alongside job state changes."""
import hashlib
import json
import re
from time import time

from catalog import MODELS


def device_id(value):
    return value if value and re.fullmatch(r"[a-f0-9]{32}", value) else None


def enqueue(db, profile, key, title, body, url, *, ping=False, ttl=1800, endpoint=None,
            device=None, public_body=None):
    now = int(time())
    expires = now + ttl
    payload = {"title": title, "body": body, "url": url, "tag": key, "profile": profile, "ping": ping}
    private = json.dumps(payload)
    public = None
    if public_body:
        token = hashlib.blake2s(key.encode(), digest_size=12).hexdigest()
        public = json.dumps({**payload, "body": public_body, "url": "/library", "profile": None, "tag": token})
    if device or public:
        db.execute("""
            INSERT OR IGNORE INTO page_notices(event_key,user_id,device_id,payload,public_payload,expires)
            VALUES (?,?,?,?,?,?)
            """, (key, profile, device, private, public, expires))
    if public:
        db.execute("""
            INSERT OR IGNORE INTO push_outbox(endpoint,event_key,payload,expires,next_attempt)
            SELECT endpoint, ?, CASE WHEN user_id=? THEN ? ELSE ? END, ?, ?
            FROM push_subscriptions WHERE mobile=1 OR last_seen>=?
            """, (key, profile, private, public, expires, now, now - 45))
    elif device or endpoint:
        db.execute("""
            INSERT OR IGNORE INTO push_outbox(endpoint,event_key,payload,expires,next_attempt)
            SELECT endpoint, ?, ?, ?, ? FROM push_subscriptions
            WHERE user_id=? AND (endpoint=? OR device_id=?)
            """, (key, private, expires, now, profile, endpoint, device))


def speech_event(db, job, state, **details):
    identifier = job["id"]
    title = job["title"][:115]
    model = MODELS.get(job["model"], job["model"])
    url = f"/library?recording={identifier}#recording-{identifier}"
    public_body = None
    if state == "queued":
        ahead = details["ahead"]
        prefix = "Speech queued"
        body = f"{title} · {model} · {ahead} {'job' if ahead == 1 else 'jobs'} ahead when submitted."
        ttl = 300
    elif state == "running":
        prefix = "Speech generation started"
        body = f"{title} · {model} · Your job has reached the front of the queue."
        ttl = 300
    elif state == "finished":
        prefix = "Speech ready"
        minutes, seconds = divmod(round(details["duration"]), 60)
        duration = f"{minutes}m {seconds:02}s" if minutes else f"{seconds}s"
        total = details["total"]
        summary = f"{model} · {total} {'chunk' if total == 1 else 'chunks'} · {duration} of audio."
        body = f"{title} · {summary}"
        ttl = 3600
        if total >= 10 or details["duration"] >= 60:
            public_body = f"A long recording finished · {summary}"
    else:
        prefix = "Speech generation stopped"
        body = f"{title} · {model} · Open Library for the error and retry options."
        ttl = 3600
    enqueue(db, job["user_id"], f"speech-{identifier}-{state}", prefix, body, url,
            ping=state == "finished", ttl=ttl, device=dict(job).get("request_device"), public_body=public_body)
