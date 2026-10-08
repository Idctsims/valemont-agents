"""core/push.py: the worker's Web Push sender, with the HTTP call stubbed.

No database and no network. The five ledger functions push.py uses are
replaced by an in-memory fake, the real pool raises on contact, and the
sender is injected, so `pywebpush.webpush` is never called for real.
"""

from __future__ import annotations

import json
import logging
import os
import unittest
from dataclasses import dataclass, field
from typing import Any
from unittest import mock

from pywebpush import WebPushException

from core import ledger, push
from core.push import Message, PushError

OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
ENV = {"VAPID_PRIVATE_KEY": "private-key-never-logged", "VAPID_SUBJECT": "mailto:owner@example.com"}


def sub(i: int, owner: str = OWNER) -> ledger.PushSubscription:
    return ledger.PushSubscription(
        id=i, owner_id=owner,
        endpoint=f"https://fcm.googleapis.com/fcm/send/SECRET-ENDPOINT-{i:04d}-tail{i:02d}",
        p256dh=f"p256dh-secret-{i}", auth=f"auth-secret-{i}", device_label=f"device {i}",
    )


@dataclass
class FakeLedger:
    subs: list[ledger.PushSubscription] = field(default_factory=list)
    notifications: dict[int, dict[str, Any]] = field(default_factory=dict)
    delivered: list[int] = field(default_factory=list)
    failed: list[tuple[int, bool]] = field(default_factory=list)

    def active_push_subscriptions(self, owner_id: str | None = None):
        return [s for s in self.subs if owner_id is None or s.owner_id == owner_id]

    def record_notification(self, **kw: Any) -> int:
        nid = 500 + len(self.notifications)
        self.notifications[nid] = {**kw, "status": "queued"}
        return nid

    def finish_notification(self, nid: int, *, status: str, error: str | None) -> None:
        self.notifications[nid].update(status=status, error=error)

    def mark_push_delivered(self, sid: int) -> None:
        self.delivered.append(sid)

    def mark_push_failed(self, sid: int, *, deactivate: bool) -> None:
        self.failed.append((sid, deactivate))


@dataclass
class Response:
    status_code: int


class Sender:
    """Records calls; fails the endpoints listed in `fail` with the given code
    (an int for an HTTP answer, an exception instance for a transport error)."""

    def __init__(self, fail: dict[int, Any] | None = None) -> None:
        self.fail = fail or {}
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kw: Any) -> None:
        self.calls.append(kw)
        kw["vapid_claims"]["aud"] = "mutated"  # what pywebpush really does
        sid = int(kw["subscription_info"]["endpoint"].split("-")[-2])
        outcome = self.fail.get(sid)
        if isinstance(outcome, int):
            raise WebPushException("Push failed", response=Response(outcome))
        if isinstance(outcome, BaseException):
            raise outcome


def _refuse_database(*a: Any, **k: Any) -> Any:
    raise AssertionError("push test reached the real database pool")


class PushTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = FakeLedger()
        for name in ("active_push_subscriptions", "record_notification", "finish_notification",
                     "mark_push_delivered", "mark_push_failed"):
            p = mock.patch.object(ledger, name, getattr(self.ledger, name))
            p.start()
            self.addCleanup(p.stop)
        for p in (mock.patch.object(ledger, "_pool", _refuse_database),
                  mock.patch.dict(os.environ, ENV)):
            p.start()
            self.addCleanup(p.stop)

    def send(self, sender: Sender, message: Message | None = None, **kw: Any):
        return push.send(message or Message(kind="test", title="Hello", body="Body",
                                            deep_link="/onboarding", tag="t1"),
                         sender=sender, **kw)


class Delivery(PushTestCase):
    def test_every_device_accepts_means_sent(self) -> None:
        self.ledger.subs = [sub(1), sub(2)]
        sender = Sender()
        [result] = self.send(sender)
        self.assertEqual(result.status, "sent")
        self.assertEqual(self.ledger.delivered, [1, 2])
        self.assertEqual(self.ledger.failed, [])
        row = self.ledger.notifications[result.notification_id]
        self.assertEqual((row["status"], row["error"], row["owner_id"], row["kind"]),
                         ("sent", None, OWNER, "test"))

    def test_payload_is_the_shape_the_service_worker_reads(self) -> None:
        self.ledger.subs = [sub(1)]
        sender = Sender()
        self.send(sender)
        call = sender.calls[0]
        self.assertEqual(json.loads(call["data"]),
                         {"title": "Hello", "body": "Body", "url": "/onboarding", "tag": "t1"})
        self.assertEqual(call["subscription_info"]["keys"],
                         {"p256dh": "p256dh-secret-1", "auth": "auth-secret-1"})
        self.assertEqual(call["vapid_private_key"], ENV["VAPID_PRIVATE_KEY"])
        self.assertGreater(call["ttl"], 0)

    def test_each_call_gets_fresh_claims(self) -> None:
        # pywebpush writes aud/exp into the dict; reusing one would send the
        # first device's audience to the second push service.
        self.ledger.subs = [sub(1), sub(2)]
        sender = Sender()
        self.send(sender)
        self.assertIsNot(sender.calls[0]["vapid_claims"], sender.calls[1]["vapid_claims"])
        self.assertEqual(sender.calls[1]["vapid_claims"]["sub"], ENV["VAPID_SUBJECT"])

    def test_a_gone_device_is_deactivated_and_the_rest_still_deliver(self) -> None:
        self.ledger.subs = [sub(1), sub(2), sub(3)]
        [result] = self.send(Sender(fail={2: 410}))
        self.assertEqual(result.status, "partial")
        self.assertEqual(self.ledger.delivered, [1, 3])
        self.assertEqual(self.ledger.failed, [(2, True)])
        self.assertIn("410", self.ledger.notifications[result.notification_id]["error"])

    def test_404_also_deactivates(self) -> None:
        self.ledger.subs = [sub(1)]
        [result] = self.send(Sender(fail={1: 404}))
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.ledger.failed, [(1, True)])
        self.assertTrue(result.deliveries[0].deactivated)

    def test_a_server_error_keeps_the_device_active(self) -> None:
        self.ledger.subs = [sub(1)]
        [result] = self.send(Sender(fail={1: 500}))
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.ledger.failed, [(1, False)])

    def test_a_transport_error_keeps_the_device_active(self) -> None:
        self.ledger.subs = [sub(1), sub(2)]
        [result] = self.send(Sender(fail={1: TimeoutError("slow")}))
        self.assertEqual(result.status, "partial")
        self.assertEqual(self.ledger.failed, [(1, False)])
        self.assertEqual(self.ledger.delivered, [2])

    def test_one_notification_row_per_owner(self) -> None:
        self.ledger.subs = [sub(1), sub(2, OTHER), sub(3)]
        results = self.send(Sender())
        self.assertEqual({r.owner_id: len(r.deliveries) for r in results}, {OWNER: 2, OTHER: 1})
        self.assertEqual(len(self.ledger.notifications), 2)

    def test_owner_filter_is_passed_through(self) -> None:
        self.ledger.subs = [sub(1), sub(2, OTHER)]
        results = self.send(Sender(), owner_id=OTHER)
        self.assertEqual([r.owner_id for r in results], [OTHER])


class Refusals(PushTestCase):
    def test_no_devices_raises_and_writes_nothing(self) -> None:
        with self.assertRaises(PushError):
            self.send(Sender())
        self.assertEqual(self.ledger.notifications, {})

    def test_missing_vapid_key_raises_before_any_read(self) -> None:
        self.ledger.subs = [sub(1)]
        with mock.patch.dict(os.environ, {"VAPID_PRIVATE_KEY": ""}):
            with self.assertRaises(PushError):
                self.send(Sender())
        self.assertEqual(self.ledger.notifications, {})

    def test_message_rejects_off_site_links_and_bad_kinds(self) -> None:
        for link in ("https://evil.example", "//evil.example", "onboarding"):
            with self.subTest(link=link), self.assertRaises(ValueError):
                Message(kind="test", title="t", deep_link=link)
        for kind in ("Test", "test-push", "", "1test"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                Message(kind=kind, title="t")


class Secrets(PushTestCase):
    def test_logs_never_carry_a_full_endpoint_or_a_key(self) -> None:
        # tests/__init__.py silences logging suite-wide; this test reads logs.
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)
        self.ledger.subs = [sub(1), sub(2), sub(3)]
        with self.assertLogs("valemont.push", level=logging.INFO) as logs:
            [result] = self.send(Sender(fail={2: 410, 3: 500}))
        text = "\n".join(logs.output) + self.ledger.notifications[result.notification_id]["error"]
        for s in self.ledger.subs:
            self.assertNotIn(s.endpoint, text)
            self.assertNotIn("SECRET-ENDPOINT", text)
            self.assertNotIn(s.p256dh, text)
            self.assertNotIn(s.auth, text)
        self.assertNotIn(ENV["VAPID_PRIVATE_KEY"], text)
        self.assertIn("fcm.googleapis.com/…", text)

    def test_short_keeps_only_host_and_tail(self) -> None:
        self.assertEqual(push.short("https://web.push.apple.com/QWERTYabcdef123456"),
                         "web.push.apple.com/…123456")
        self.assertEqual(push.short("not a url"), "…")


if __name__ == "__main__":
    unittest.main()
