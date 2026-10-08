"""Web Push from the worker (db/019, VAPID).

The second of two senders sharing one pair of tables: apps/web sends the
"Send test push" from the owner's session; this module sends everything the
worker originates (job-health alerts and the Morning Brief, from Phase 4).
Both log to `notifications` and read `push_subscriptions`; both send the same
JSON payload, which the service worker (apps/web/src/app/sw.ts) renders:

    {"title": ..., "body": ..., "url": "/same-origin/path", "tag": ...}

Per message and per owner: one `notifications` row, written `queued` before any
device is tried and finished as `sent` (every device accepted), `partial`
(some did) or `failed` (none did). Each device is tried independently, so one
dead phone never blocks the others. A 404 or 410 from the push service means
the subscription is gone for good, and it is marked inactive. Any other
failure only stamps `failed_at`, and the next send tries it again.

Never logged: keys, auth secrets, or a full endpoint (it is a capability:
whoever holds it plus the VAPID private key can push to that device).
Endpoints appear truncated, via `short()`.

All SQL lives in core/ledger.py. Nothing here schedules anything: wiring this
into the scheduler is Chat 1 Phase 4.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from pywebpush import WebPushException, webpush

from core import ledger

log = logging.getLogger("valemont.push")

#: How long the push service keeps a message for an offline device.
TTL_SECONDS = 60 * 60 * 12

_KIND = re.compile(r"^[a-z][a-z0-9_]*$")


class PushError(RuntimeError):
    """Raised when a send cannot even be attempted (no keys, no devices)."""


@dataclass(frozen=True)
class Message:
    kind: str
    title: str
    body: str | None = None
    #: Same-origin path the notification opens. Anything else is refused here,
    #: mirroring the CHECK in db/019 and the service worker's own guard.
    deep_link: str | None = None
    #: Replaces an earlier notification with the same tag on the device.
    tag: str | None = None

    def __post_init__(self) -> None:
        if not _KIND.match(self.kind):
            raise ValueError(f"kind must be snake_case, got {self.kind!r}")
        if not self.title:
            raise ValueError("title is required")
        if self.deep_link is not None and (
            not self.deep_link.startswith("/") or self.deep_link.startswith("//")
        ):
            raise ValueError("deep_link must be a same-origin path like /command")

    def payload(self) -> str:
        return json.dumps({
            "title": self.title,
            "body": self.body or "",
            "url": self.deep_link or "/",
            **({"tag": self.tag} if self.tag else {}),
        })


@dataclass(frozen=True)
class Delivery:
    subscription_id: int
    endpoint: str  # already truncated
    ok: bool
    status_code: int | None
    deactivated: bool


@dataclass(frozen=True)
class SendResult:
    owner_id: str
    notification_id: int
    status: ledger.NotificationStatus
    deliveries: tuple[Delivery, ...]


def short(endpoint: str) -> str:
    """`fcm.googleapis.com/…a1b2c3` style: host and the last six characters."""
    parts = urlsplit(endpoint)
    return f"{parts.netloc}/…{endpoint[-6:]}" if parts.netloc else "…"


def _vapid() -> tuple[str, str]:
    key = os.getenv("VAPID_PRIVATE_KEY")
    subject = os.getenv("VAPID_SUBJECT")
    if not key or not subject:
        raise PushError("VAPID_PRIVATE_KEY and VAPID_SUBJECT must be set (root .env, or Railway variables).")
    if not subject.startswith(("mailto:", "https://")):
        raise PushError("VAPID_SUBJECT must be a mailto: or https: URL.")
    return key, subject


Sender = Callable[..., object]


def send(message: Message, *, owner_id: str | None = None,
         sender: Sender = webpush) -> list[SendResult]:
    """Push `message` to every active device (of `owner_id`, or of everyone).

    Raises PushError when there is nothing to send to: a notification nobody
    can receive is an outage to surface, not a row to write quietly.
    """
    key, subject = _vapid()
    subscriptions = ledger.active_push_subscriptions(owner_id)
    if not subscriptions:
        raise PushError("no active push subscriptions; enable notifications on /onboarding first")

    by_owner: dict[str, list[ledger.PushSubscription]] = {}
    for s in subscriptions:
        by_owner.setdefault(s.owner_id, []).append(s)

    results = []
    for owner, subs in by_owner.items():
        nid = ledger.record_notification(
            owner_id=owner, kind=message.kind, title=message.title,
            body=message.body, deep_link=message.deep_link,
        )
        deliveries = tuple(_deliver(sub, message, key, subject, sender) for sub in subs)
        ok = sum(d.ok for d in deliveries)
        status: ledger.NotificationStatus = (
            "sent" if ok == len(deliveries) else "partial" if ok else "failed"
        )
        failures = [f"{d.endpoint}: {d.status_code or 'error'}" for d in deliveries if not d.ok]
        ledger.finish_notification(nid, status=status, error="; ".join(failures) or None)
        log.info("push %s #%d %s: %d/%d delivered", message.kind, nid, status, ok, len(deliveries))
        results.append(SendResult(owner, nid, status, deliveries))
    return results


def _deliver(sub: ledger.PushSubscription, message: Message, key: str, subject: str,
             sender: Sender) -> Delivery:
    endpoint = short(sub.endpoint)
    try:
        sender(
            subscription_info={"endpoint": sub.endpoint,
                               "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=message.payload(),
            vapid_private_key=key,
            # A fresh dict per call: pywebpush writes `aud` and `exp` into it.
            vapid_claims={"sub": subject},
            ttl=TTL_SECONDS,
            timeout=10,
        )
    except WebPushException as exc:
        code = exc.response.status_code if exc.response is not None else None
        gone = code in (404, 410)
        ledger.mark_push_failed(sub.id, deactivate=gone)
        if gone:
            log.warning("push %s: subscription gone (%s), marked inactive", endpoint, code)
        else:
            log.error("push %s failed: HTTP %s", endpoint, code)
        return Delivery(sub.id, endpoint, False, code, gone)
    except Exception as exc:  # network, TLS, timeout: try again next send
        ledger.mark_push_failed(sub.id, deactivate=False)
        log.error("push %s failed: %s", endpoint, type(exc).__name__)
        return Delivery(sub.id, endpoint, False, None, False)
    ledger.mark_push_delivered(sub.id)
    return Delivery(sub.id, endpoint, True, 201, False)
