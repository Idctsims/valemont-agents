"""The Odds API credit guard (Chat 8 Phase 2; owner, Nov 9). Core's, so no
adapter or job can spend around it (CLAUDE.md §3).

    refuse a paid call  when the last reported remaining < 50, or this
                        month's spend plus the call's cost would pass 450.
                        The spend is the ACCOUNT's own count: the latest
                        x-requests-used header (odds_api_usage().used,
                        db/074; owner, Nov 9), not the odds_api_calls sum,
                        which misses Step 0's 11 credits. The table stays
                        the call log.
    push once a month   at a spend of 400 ("odds_credits_400")
    fail loud once      the first refusal of a month pushes
                        ("odds_credits_refused") and fails its run; later
                        refusals that month are noted, not failed (the
                        health monitor already has the incident)

The month is odds_api_usage()'s (calendar, UTC); the account's own reset
date may differ (the owner reads it off the account page). The allowance is
the free tier's 500.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from core import ledger, push
from venues.odds.client import Call

log = logging.getLogger("valemont.odds_credits")

ALLOWANCE = 500
REFUSE_REMAINING = 50
REFUSE_MONTH = 450
PUSH_AT = 400
KIND_400 = "odds_credits_400"
KIND_REFUSED = "odds_credits_refused"


class CreditRefused(RuntimeError):
    """A paid Odds API call refused by the guard."""


@dataclass
class CreditGuard:
    """One run's view: the month's spend and remaining as the database had
    them at the start, plus this run's own calls."""

    #: The account's used count at the start (the latest x-requests-used).
    spent_at_start: int
    remaining_at_start: int | None
    calls: list[Call] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, usage: Callable[[], dict[str, Any]] | None = None) -> "CreditGuard":
        u = (usage or ledger.odds_api_usage)()
        used = u.get("used")
        # Before db/074 (no `used`): the call log's sum, the only count there was.
        start = int(used) if used is not None else int(u.get("spent") or 0)
        return cls(start, None if u.get("remaining") is None else int(u["remaining"]))

    @property
    def spent(self) -> int:
        """The account's used count: this run's latest header, else the start's
        plus this run's costs (a call that reported no header)."""
        for c in reversed(self.calls):
            if c.used is not None:
                return c.used + sum(x.cost or 0 for x in self.calls[self.calls.index(c) + 1:])
        return self.spent_at_start + sum(c.cost or 0 for c in self.calls)

    @property
    def remaining(self) -> int | None:
        for c in reversed(self.calls):
            if c.remaining is not None:
                return c.remaining
        return self.remaining_at_start

    def check(self, cost: int) -> None:
        rem = self.remaining
        if rem is not None and rem < REFUSE_REMAINING:
            why = f"remaining {rem} < {REFUSE_REMAINING}"
        elif self.spent + cost > REFUSE_MONTH:
            why = f"this month's spend {self.spent} + {cost} would pass {REFUSE_MONTH}"
        else:
            return
        self.refused.append(why)
        raise CreditRefused(f"Odds API paid call refused: {why}")

    def note(self, call: Call) -> None:
        self.calls.append(call)


def recorder(guard: CreditGuard, write: Callable[[dict[str, Any]], None] | None = None) -> Callable[[Call], None]:
    """The client's record callback: the row to odds_api_calls, then the guard's tally."""
    def record(call: Call) -> None:
        guard.note(call)
        (write or ledger.odds_api_call_record)(call.row())
    return record


def _push_once(kind: str, title: str, body: str) -> bool:
    """One push per kind per month (owner's month). Never raises."""
    try:
        if ledger.notification_sent_this_month(kind):
            return False
        push.send(push.Message(kind=kind, title=title, body=body, deep_link="/betting", tag="odds-credits"))
        return True
    except Exception as exc:
        log.error("odds credit push %s not sent: %s: %s", kind, type(exc).__name__, exc)
        return False


def after_run(guard: CreditGuard) -> bool:
    """The month's pushes. Returns True when this run's refusal is the
    month's first (the run then fails loud)."""
    if guard.spent >= PUSH_AT:
        _push_once(KIND_400, "Odds API credits at 400",
                   f"{guard.spent} of {ALLOWANCE} used this month; paid calls stop past {REFUSE_MONTH}.")
    if guard.refused:
        return _push_once(KIND_REFUSED, "Odds API calls refused",
                          f"{guard.refused[0]}. The consensus runs on ESPN and Kalshi until the credits reset.")
    return False
