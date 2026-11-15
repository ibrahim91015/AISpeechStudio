"""Create durable notification deliveries alongside job state changes."""
import json
from time import time

from catalog import MODELS


def enqueue(db, profile, key, title, body, url, *, ping=False, ttl=1800, endpoint=None):
    now = int(time())
    db.execute("""
        INSERT OR IGNORE INTO push_outbox(endpoint,event_key,payload,expires,next_attempt)
        SELECT endpoint, ?, ?, ?, ? FROM push_subscriptions
        WHERE user_id=? AND (? IS NULL OR endpoint=?)
        """, (key, json.dumps({"title": title, "body": body, "url": url, "tag": key,
                               "profile": profile, "ping": ping}), now + ttl, now,
              profile, endpoint, endpoint))


def speech_event(db, job, state, **details):
    identifier = job["id"]
    title = job["title"][:115]
    model = MODELS.get(job["model"], job["model"])
    url = f"/library?recording={identifier}#recording-{identifier}"
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
        body = f"{title} · {model} · {total} {'chunk' if total == 1 else 'chunks'} · {duration} of audio."
        ttl = 3600
    else:
        prefix = "Speech generation stopped"
        body = f"{title} · {model} · Open Library for the error and retry options."
        ttl = 3600
    enqueue(db, job["user_id"], f"speech-{identifier}-{state}", prefix, body, url,
            ping=state == "finished", ttl=ttl)
