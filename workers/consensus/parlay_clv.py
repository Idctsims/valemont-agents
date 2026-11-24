"""The CLV (proxy) summary: per agent, never without its ages (P2.1 §6; owner, 2026-11-24).

Every CLV (proxy) figure is reported with the age of the closes behind it: per
agent, the median and the maximum age (minutes from a leg's last candle to its
kickoff) beside the mean. A row whose legs carry no age cannot be summarized:
`summarize` raises rather than report a mean without one.

Cohorts never pool (§0, §1, §9.2): rows group by (agent, scope, rule version),
so P2 and P2.1 are separate lines, and the cross-sport Lotto is its own. A P2
close captured before ages were recorded carries each leg's kickoff and
candle end, so its age is derived from them, never assumed.

Pure: the caller reads the rows (closing_snapshots joined to commitments).
The proxy is not profit (CLAUDE.md §8, §10), and no figure here combines agents.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping

__all__ = ["Line", "leg_ages", "summarize"]


class MissingAges(ValueError):
    """A captured close whose legs carry no age: no CLV figure may be reported from it."""


@dataclass(frozen=True)
class Line:
    agent: str
    scope: str                 # sport | cross_sport
    rule_version: str          # the cohort: P2, P2.1, ...
    tickets: int
    mean_clv: Decimal
    mean_clv_pct: Decimal | None
    legs: int
    median_age_min: float
    max_age_min: float

    def text(self) -> str:
        """One line, the ages beside the mean, labelled a proxy."""
        return (f"{self.agent} ({self.scope}, {self.rule_version}): CLV (proxy) mean {self.mean_clv:+.4f} over "
                f"{self.tickets} tickets; close age median {self.median_age_min:g} min, max {self.max_age_min:g} min")


def leg_ages(detail: Mapping[str, Any]) -> list[float]:
    """Each captured leg's close age in minutes: recorded (`age_min`), else derived
    from its kickoff and candle end. Raises MissingAges if a leg has neither."""
    legs = detail.get("legs") or []
    if not legs:
        raise MissingAges("a captured close with no legs in its detail")
    out = []
    for leg in legs:
        if leg.get("age_min") is not None:
            out.append(float(leg["age_min"]))
        elif leg.get("kickoff") and leg.get("candle_end"):
            delta = datetime.fromisoformat(leg["kickoff"]) - datetime.fromisoformat(leg["candle_end"])
            out.append(round(delta.total_seconds() / 60, 1))
        else:
            raise MissingAges(f"leg {leg.get('ticker')} has no close age")
    return out


def summarize(rows: Iterable[Mapping[str, Any]]) -> list[Line]:
    """Rows: {agent, scope, rule_version, clv, clv_pct, detail} for captured closes
    only. One Line per (agent, scope, rule version), sorted; never a blend."""
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for r in rows:
        key = (r["agent"], r.get("scope") or "sport", r.get("rule_version") or "P2")
        groups.setdefault(key, []).append(r)
    out = []
    for (agent, scope, version), rs in sorted(groups.items()):
        ages = [a for r in rs for a in leg_ages(r["detail"])]
        clvs = [Decimal(str(r["clv"])) for r in rs]
        pcts = [Decimal(str(r["clv_pct"])) for r in rs if r.get("clv_pct") is not None]
        out.append(Line(agent, scope, version, len(rs), sum(clvs) / len(clvs),
                        (sum(pcts) / len(pcts)) if pcts else None, len(ages),
                        statistics.median(ages), max(ages)))
    return out
