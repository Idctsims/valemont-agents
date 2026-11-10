"""The Kalshi board refresh's priority (owner, Nov 9, on PR #106): inside its
existing 300-call cap and 3 h window, events with an outside price (a books
or espn leg_consensus row) are re-priced first, then the soonest start. No
cap or window change.

Process memory, shared between the consensus job (which writes it after
every run) and the refresh (which reads it). A restart empties it until the
next consensus run (10:15 or 16:15 Chicago); the refresh then orders by start
alone, as before. Never read back from the database (the board job's rule).
"""

from __future__ import annotations

import threading
from typing import Iterable

_LOCK = threading.Lock()
_EVENTS: frozenset[str] = frozenset()


def set_events(events: Iterable[str]) -> None:
    global _EVENTS
    with _LOCK:
        _EVENTS = frozenset(events)


def events() -> frozenset[str]:
    with _LOCK:
        return _EVENTS
