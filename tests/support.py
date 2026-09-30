"""Shared fixtures: an in-memory ledger and the factories the suites build on.

Nothing here touches Postgres or the network. `LedgerTestCase` replaces every
public function in `core.ledger` with a recording stub, makes the real
connection pool raise on contact, and makes `urllib` raise on contact. A test
that reaches either has found a code path the stub does not cover, and fails.

Every write the stub accepts is attributed to an agent. After each test,
`LedgerTestCase` asserts that none of them belonged to a real (non-test)
agent — the same quarantine `agents.is_test` enforces in the database, applied
to the suite itself.
"""

from __future__ import annotations

import itertools
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, ClassVar, Sequence
from unittest import mock

from core import ledger
from core.agent import BaseAgent, DeferPolicy, Proposal, Verdict
from core.ledger import Leg, LegOutcome, PendingCommitment

#: Mirrors the seeded roster. Real agents are is_test = false.
REAL_AGENTS: dict[str, int] = {
    "crypto": 1, "equities": 2, "prizepicks": 3, "kalshi": 5,
}
#: Harness agents the suites run as. is_test = true.
TEST_AGENTS: dict[str, int] = {"_fake": 4, "_test": 90, "_test_crypto": 91,
                               "_test_nfl_ml": 92}

WRITES = frozenset({
    "start_run", "end_run", "commit", "add_resolution", "emit_event",
    "record_resolution_attempt", "add_closing_snapshot", "record_selection",
    "record_model_version",
})
READS = frozenset({
    "agent_id", "agent_is_test", "agent_enabled", "due_for_resolution",
    "due_for_capture",
    "open_commitments", "latest_model_version",
})


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Call:
    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    ok: bool = True
    agent_id: int | None = None


@dataclass
class LedgerStub:
    """Records every ledger call; serves scripted reads; injects failures."""

    calls: list[Call] = field(default_factory=list)
    due: list[PendingCommitment] = field(default_factory=list)
    capture: list[PendingCommitment] = field(default_factory=list)
    open: list[PendingCommitment] = field(default_factory=list)
    model_versions: list[ledger.ModelVersion] = field(default_factory=list)
    agents: dict[str, tuple[int, bool]] = field(default_factory=dict)
    #: Slugs whose `agents.enabled` is false. Everything else reads as enabled.
    disabled: set[str] = field(default_factory=set)
    _failures: dict[str, list[tuple[Callable[[Call], bool], BaseException]]] = (
        field(default_factory=dict)
    )
    _runs: dict[int, int] = field(default_factory=dict)
    _commitments: dict[int, int] = field(default_factory=dict)
    _ids: Any = field(default_factory=lambda: itertools.count(1000))

    def __post_init__(self) -> None:
        self.agents.update({s: (i, False) for s, i in REAL_AGENTS.items()})
        self.agents.update({s: (i, True) for s, i in TEST_AGENTS.items()})

    # -- configuration ------------------------------------------------------

    def fail_on(
        self,
        name: str,
        exc: BaseException,
        when: Callable[[Call], bool] = lambda call: True,
    ) -> None:
        self._failures.setdefault(name, []).append((when, exc))

    def add_due(self, *pending: PendingCommitment) -> None:
        for p in pending:
            self._commitments[p.id] = p.agent_id
        self.due.extend(pending)

    def add_capture(self, *pending: PendingCommitment) -> None:
        for p in pending:
            self._commitments[p.id] = p.agent_id
        self.capture.extend(pending)

    # -- queries for assertions ---------------------------------------------

    def named(self, name: str, *, ok: bool | None = True) -> list[Call]:
        return [
            c for c in self.calls
            if c.name == name and (ok is None or c.ok is ok)
        ]

    def writes(self) -> list[Call]:
        return [c for c in self.calls if c.name in WRITES and c.ok]

    def events(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (c.args[0], c.kwargs.get("detail") or {})
            for c in self.named("emit_event")
        ]

    def event_kinds(self) -> list[str]:
        return [kind for kind, _ in self.events()]

    def writes_to_non_test(self) -> list[Call]:
        """Accepted writes whose agent is real, or cannot be proven a test."""
        test_ids = {i for i, is_test in self.agents.values() if is_test}
        return [c for c in self.writes() if c.agent_id not in test_ids]

    # -- the stubbed functions ----------------------------------------------

    def _record(self, name: str, args: tuple, kwargs: dict, agent_id: int | None) -> Call:
        call = Call(name, args, kwargs, agent_id=agent_id)
        self.calls.append(call)
        for when, exc in self._failures.get(name, ()):
            if when(call):
                call.ok = False
                raise exc
        return call

    def agent_id(self, slug: str) -> int:
        self._record("agent_id", (slug,), {}, None)
        if slug not in self.agents:
            raise ledger.LedgerError(f"No agent registered with slug {slug!r}.")
        return self.agents[slug][0]

    def agent_is_test(self, slug: str) -> bool:
        self._record("agent_is_test", (slug,), {}, None)
        if slug not in self.agents:
            raise ledger.LedgerError(f"No agent registered with slug {slug!r}.")
        return self.agents[slug][1]

    def agent_enabled(self, slug: str) -> bool:
        self._record("agent_enabled", (slug,), {}, None)
        if slug not in self.agents:
            raise ledger.LedgerError(f"No agent registered with slug {slug!r}.")
        return slug not in self.disabled

    def latest_model_version(self, agent_id: int, model: str,
                             before: datetime) -> ledger.ModelVersion | None:
        self._record("latest_model_version", (agent_id, model, before), {}, agent_id)
        usable = [m for m in self.model_versions
                  if m.agent_id == agent_id and m.model == model and m.data_through < before]
        return max(usable, key=lambda m: (m.data_through, m.id)) if usable else None

    def record_model_version(self, **kwargs: Any) -> int:
        self._record("record_model_version", (), kwargs, kwargs.get("agent_id"))
        return next(self._ids)

    def start_run(self, agent_id: int, notes: str | None = None) -> int:
        self._record("start_run", (agent_id,), {"notes": notes}, agent_id)
        run_id = next(self._ids)
        self._runs[run_id] = agent_id
        return run_id

    def end_run(self, run_id: int, status: str, error: str | None = None,
                notes: str | None = None) -> None:
        self._record("end_run", (run_id, status),
                     {"error": error, "notes": notes}, self._runs.get(run_id))

    def commit(self, **kwargs: Any) -> ledger.Commitment:
        self._record("commit", (), kwargs, kwargs.get("agent_id"))
        if not kwargs.get("legs"):
            raise ledger.LedgerError("A commitment with no legs is not a commitment.")
        commitment_id = next(self._ids)
        self._commitments[commitment_id] = kwargs["agent_id"]
        return ledger.Commitment(
            id=commitment_id, committed_at=now(),
            resolves_after=kwargs["resolves_after"],
        )

    def add_resolution(self, **kwargs: Any) -> int:
        self._record("add_resolution", (), kwargs,
                     self._commitments.get(kwargs.get("commitment_id")))
        return next(self._ids)

    def emit_event(self, kind: str, message: str | None = None, **kwargs: Any) -> None:
        self._record("emit_event", (kind, message), kwargs, kwargs.get("agent_id"))

    def record_resolution_attempt(self, commitment_id: int, result: str = "deferred",
                                  reason: str | None = None,
                                  purpose: str = "resolve") -> None:
        self._record("record_resolution_attempt", (commitment_id, result),
                     {"reason": reason, "purpose": purpose},
                     self._commitments.get(commitment_id))

    def add_closing_snapshot(self, **kwargs: Any) -> ledger.ClosingSnapshot:
        self._record("add_closing_snapshot", (), kwargs,
                     self._commitments.get(kwargs.get("commitment_id")))
        return ledger.ClosingSnapshot(
            id=next(self._ids), commitment_id=kwargs["commitment_id"],
            captured_at=now(), status=kwargs.get("status", "captured"),
            clv=kwargs.get("clv"), clv_pct=kwargs.get("clv_pct"),
        )

    def record_selection(self, commitment_id: int, selected: bool,
                         note: str | None = None) -> int:
        self._record("record_selection", (commitment_id, selected),
                     {"note": note}, self._commitments.get(commitment_id))
        return next(self._ids)

    def due_for_resolution(self, agent_id: int | None = None, limit: int = 100,
                           **kwargs: Any) -> list[PendingCommitment]:
        self._record("due_for_resolution", (), {"agent_id": agent_id}, agent_id)
        return [p for p in self.due if agent_id is None or p.agent_id == agent_id][:limit]

    def due_for_capture(self, agent_id: int | None = None, limit: int = 100,
                        **kwargs: Any) -> list[PendingCommitment]:
        self._record("due_for_capture", (), {"agent_id": agent_id}, agent_id)
        return [p for p in self.capture if agent_id is None or p.agent_id == agent_id][:limit]

    def open_commitments(self, agent_id: int | None = None, limit: int = 200,
                         **kwargs: Any) -> list[PendingCommitment]:
        self._record("open_commitments", (), {"agent_id": agent_id}, agent_id)
        return [p for p in self.open if agent_id is None or p.agent_id == agent_id][:limit]


def _refuse_database(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError(
        "a test reached core.ledger._pool() — some ledger call is not stubbed"
    )


def _refuse_network(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a test attempted a real HTTP request")


class LedgerTestCase(unittest.TestCase):
    """Installs a fresh `LedgerStub` per test and enforces the quarantine."""

    def setUp(self) -> None:
        super().setUp()
        self.ledger = LedgerStub()
        for name in WRITES | READS:
            patcher = mock.patch.object(ledger, name, getattr(self.ledger, name))
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (
            mock.patch.object(ledger, "_pool", _refuse_database),
            mock.patch("urllib.request.urlopen", _refuse_network),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        leaked = self.ledger.writes_to_non_test()
        self.assertEqual(
            leaked, [],
            f"{len(leaked)} ledger write(s) attributed to a non-test agent: "
            f"{[(c.name, c.agent_id) for c in leaked]}",
        )
        super().tearDown()


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def pending(
    commitment_id: int,
    *,
    agent_id: int = TEST_AGENTS["_test"],
    slug: str = "_test",
    legs: Sequence[Leg] = (Leg("SUBJ", "market", Decimal("0.40"), "yes", Decimal("1")),),
    payload: dict[str, Any] | None = None,
    kind: ledger.Kind = "event_contract",
    attempts: int = 0,
    overdue_by: timedelta = timedelta(0),
    committed_at: datetime | None = None,
    closes_at: datetime | None = None,
) -> PendingCommitment:
    committed = committed_at or now() - timedelta(hours=1)
    return PendingCommitment(
        id=commitment_id, agent_id=agent_id, agent_slug=slug, is_test=True,
        kind=kind, thesis=f"scripted #{commitment_id}", confidence=None,
        payload=payload or {}, committed_at=committed,
        resolves_after=committed + timedelta(minutes=30), legs=tuple(legs),
        closes_at=closes_at, attempts=attempts, overdue_by=overdue_by,
    )


def proposal(
    subject: str = "Player A",
    market: str = "points_over",
    line: Any = Decimal("25.5"),
    direction: ledger.Direction = "over",
    *,
    thesis: str | None = None,
    extra_legs: Sequence[Leg] = (),
) -> Proposal:
    return Proposal(
        kind="prop_slip",
        thesis=thesis or f"{subject} {market} {direction} {line}",
        payload={"stake": "1", "invalidation": "0"},
        resolves_after=now() + timedelta(hours=1),
        legs=[Leg(subject, market, line, direction, Decimal("1")), *extra_legs],
    )


def hit(pnl: Any = Decimal("1")) -> Verdict:
    return Verdict(
        outcome="hit",
        leg_outcomes=[LegOutcome(leg_index=0, outcome="hit", actual=Decimal("1"))],
        pnl=pnl,
    )


class ScriptedAgent(BaseAgent[None, str]):
    """A test agent whose four hooks do exactly what the test says."""

    slug: ClassVar[str] = "_test"

    def __init__(
        self,
        *,
        proposals: Proposal | Sequence[Proposal] | None = None,
        resolver: Callable[[PendingCommitment], Verdict | None] | None = None,
        closer: Callable[[PendingCommitment], Any] | None = None,
        captures_close: bool = False,
        defer_policy: DeferPolicy | None = None,
        capture_policy: DeferPolicy | None = None,
        max_slate_size: int | None = None,
    ) -> None:
        super().__init__(defer_policy, capture_policy, max_slate_size)
        self.proposals = proposals
        self.resolver = resolver or (lambda p: hit())
        self.closer = closer or (lambda p: None)
        self.captures_close = captures_close
        self.resolve_calls: list[int] = []
        self.capture_calls: list[int] = []

    def observe(self) -> None:
        return None

    def form_thesis(self, observation: None) -> str | None:
        return "scripted"

    def build_commitment(self, thesis: str) -> Any:
        return self.proposals

    def resolve(self, pending: PendingCommitment) -> Verdict | None:
        self.resolve_calls.append(pending.id)
        return self.resolver(pending)

    def capture_close(self, pending: PendingCommitment) -> Any:
        self.capture_calls.append(pending.id)
        return self.closer(pending)
