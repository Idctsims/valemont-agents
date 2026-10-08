"""Send one test push through the WORKER path (core/push.py), from this machine.

    ..\\venv\\Scripts\\python.exe -m scripts.send_test_push        (from workers/)

Proves the second sender end to end: root .env VAPID keys, active devices read
through core/ledger.py, a `notifications` row per owner, 404/410 handling.
It needs a device subscribed first (/onboarding → Enable notifications).

Not scheduled anywhere. Wiring push into the Railway scheduler is Chat 1
Phase 4. Prints truncated endpoints only, never keys.
"""

from __future__ import annotations

import logging
import sys

from core import ledger
from core.paths import load_env
from core.push import Message, PushError, send


def main() -> int:
    load_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    message = Message(
        kind="test",
        title="Valemont test push",
        body="Sent by the worker (scripts.send_test_push).",
        deep_link="/onboarding",
        tag="test-push",
    )
    try:
        results = send(message)
    except PushError as exc:
        print(f"Not sent: {exc}", file=sys.stderr)
        return 1
    finally:
        ledger.close_pool()

    for r in results:
        print(f"notification #{r.notification_id}: {r.status}")
        for d in r.deliveries:
            state = "delivered" if d.ok else f"failed ({d.status_code or 'error'})"
            gone = ", marked inactive" if d.deactivated else ""
            print(f"  {d.endpoint}  {state}{gone}")
    return 0 if all(r.status == "sent" for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
