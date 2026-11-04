"""The outcome check in the pool (owner, 2026-10-20: "record checks, the
picker gates").

Item 5 (docs/arsenal.md §8c) asks of every reference: does its real ending
contradict what its deploy line asks the listener to believe? Until now it
ran by hand (scripts.arsenal_outcome_check), so a batch could drop before
anyone ran it: batch run 38's references were never checked, and two of them
dropped (Oct 17 and 19). Now:

  * every check is recorded in arsenal_outcome_checks (db/057, append-only):
    the votes, the page read, the note, the controls that passed in the same
    run, and who ran it. A conflict also writes an arsenal_holds row, in the
    same transaction (ledger.arsenal_record_outcome).
  * the picker takes only a reference with a check on record. A released
    hold makes a conflicting reference eligible again (db/056).
  * the worker runs it (check_unchecked) on every ready real reference with
    no check yet: queued when a batch's verification closes
    (verify.verify_item), inline after a top-up (pool.topup), and queued by
    arsenal_pool whenever unchecked references remain.

The three controls (tests/fixtures/arsenal/outcome_controls.json:
Thermopylae and Custer must flag, the Miracle on Ice must not) run first, in
the same run, through the same retrieval and vote. If any comes out wrong,
they run once more (owner, Nov 4); if the rerun has one wrong too, NOTHING
is recorded and ControlsFailed is raised: the references stay unchecked, so
none of them can drop, and job_health shows the failure. Every failed
control is logged with its votes and reason.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from arsenal import sources, verify
from core import ledger

log = logging.getLogger("valemont.arsenal.outcome")

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "arsenal" / "outcome_controls.json"
#: The worker's checks; the script records 'script', db/057's backfill 'backfill'.
SOURCE = "worker"


class ControlsFailed(RuntimeError):
    """A control came out wrong in this run: nothing was recorded."""


# ------------------------------------------------------------------ controls

def card(c: Mapping[str, Any]) -> ledger.ArsenalRefToVerify:
    return ledger.ArsenalRefToVerify(
        int(c["id"]), c["category"], c["title"], c["origin"], c["source_work"], None, None, c["source_kind"],
        c.get("source_page"), "verified", None, c["meaning"], c["deploy_line"])


def cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def votes_text(votes: tuple[bool, ...]) -> str:
    """One letter a read, as stored: Y a conflict, n none; '' for no read."""
    return "".join("Y" if v else "n" for v in votes)


@dataclass(frozen=True)
class ControlResult:
    name: str
    ok: bool
    votes: str  # votes_text, '-' for no read
    note: str   # the reader's reason
    line: str   # the whole PASS/FAIL line, as printed and logged


def control_results(*, ask: verify.AskOutcome, samples: int = verify.OUTCOME_SAMPLES,
                    http: sources.Http | None = None) -> list[ControlResult]:
    """One pass over the three controls."""
    out = []
    for case in cases():
        # Each control reads its pinned page (owner, Nov 4): the controls test the judge, not retrieval.
        res = verify.outcome_check(card(case["card"]), ask=ask, samples=samples, http=http,
                                   page_title=case["pinned_page"])
        ok = res.conflict == case["expect_conflict"]
        votes = votes_text(res.votes) or "-"
        line = (f"{'PASS' if ok else 'FAIL'} {case['name']}: expect {'conflict' if case['expect_conflict'] else 'ok'}, "
                f"got {'conflict' if res.conflict else 'ok'} (votes {votes}; page {res.page}); {res.note}")
        out.append(ControlResult(case["name"], ok, votes, res.note, line))
    return out


def run_controls(*, ask: verify.AskOutcome, samples: int = verify.OUTCOME_SAMPLES,
                 http: sources.Http | None = None) -> tuple[int, list[str]]:
    """(how many came out wrong, one line per case). One pass, no rerun."""
    results = control_results(ask=ask, samples=samples, http=http)
    return sum(not r.ok for r in results), [r.line for r in results]


def _failed(results: list[ControlResult]) -> str:
    return ", ".join(f"{r.name} (votes {r.votes})" for r in results if not r.ok)


def controls_gate(*, ask: verify.AskOutcome, samples: int = verify.OUTCOME_SAMPLES,
                  http: sources.Http | None = None, echo: Callable[[str], None] | None = None) -> str:
    """Run the controls; return "passed/total", or raise ControlsFailed.

    On a wrong control the controls run ONCE more before refusing (owner,
    Nov 4: Haiku's single reads swing, and one wrong vote refused three runs
    that day). Every failed control is logged with its votes and reason; the
    rerun must pass all of them, so the recorded "n/n" is still every control
    passing in one pass of the same run (db/057). A control wrong in both
    passes is named in the refusal: one that keeps failing gets replaced.
    `echo` (a script's print) sees every line too."""
    def one_pass(label: str) -> list[ControlResult]:
        results = control_results(ask=ask, samples=samples, http=http)
        for r in results:
            log.info("arsenal outcome control (%s): %s", label, r.line)
            if echo:
                echo(r.line)
            if not r.ok:
                log.warning("arsenal outcome control FAILED (%s): %s, votes %s; reason: %s",
                            label, r.name, r.votes, r.note)
        return results

    first = one_pass("run")
    total = len(first)
    if all(r.ok for r in first):
        return f"{total}/{total}"
    log.warning("arsenal outcome check: %d of %d controls wrong (%s); re-running the controls once",
                sum(not r.ok for r in first), total, _failed(first))
    second = one_pass("rerun")
    if all(r.ok for r in second):
        log.warning("arsenal outcome check: controls passed on the rerun (first pass wrong: %s)", _failed(first))
        return f"{total}/{total}"
    both = [r.name for r in second if not r.ok and not next(f for f in first if f.name == r.name).ok]
    msg = (f"{sum(not r.ok for r in second)} of {total} controls wrong in this run, after one rerun "
           f"(rerun: {_failed(second)}; first pass: {_failed(first)})"
           + (f"; wrong in both: {', '.join(both)}" if both else "") + "; nothing recorded")
    log.error("arsenal outcome check: %s", msg)
    raise ControlsFailed(msg)


# -------------------------------------------------------------------- record

def record(ref_id: int, res: verify.OutcomeResult, *, controls: str, source: str) -> int | None:
    """One check on the record; a conflict also holds (one transaction).
    Returns the hold's id, or None."""
    _, hold_id = ledger.arsenal_record_outcome(
        ref_id, conflict=res.conflict, votes=votes_text(res.votes), page=res.page, note=res.note,
        controls=controls, source=source)
    return hold_id


# ----------------------------------------------------------------------- run

@dataclass(frozen=True)
class OutcomeRun:
    job_note: str
    checked: int = 0
    held: int = 0


def check_unchecked(*, ask: verify.AskOutcome | None = None, http: sources.Http | None = None,
                    samples: int = verify.OUTCOME_SAMPLES, client: Any = None) -> OutcomeRun:
    """Check every ready real reference with no check on record (none: no
    AI call at all). Controls first; ControlsFailed records nothing."""
    refs = ledger.arsenal_unchecked_refs()
    if not refs:
        return OutcomeRun("nothing to check")
    ask = ask or (lambda r, e: verify.ask_outcome_haiku(r, e, client=client))
    controls = controls_gate(ask=ask, samples=samples, http=http)
    held = 0
    for ref in refs:
        res = verify.outcome_check(ref, ask=ask, http=http, samples=samples)
        hold_id = record(ref.id, res, controls=controls, source=SOURCE)
        held += hold_id is not None
        log.info("arsenal outcome check: %d %r: %s votes %s; page %s; %s%s", ref.id, ref.title,
                 "CONFLICT" if res.conflict else "ok", votes_text(res.votes) or "-", res.page, res.note,
                 f" (held: arsenal_holds {hold_id})" if hold_id is not None else "")
    return OutcomeRun(f"controls {controls}; checked {len(refs)}, held {held}", len(refs), held)


def outcome_job(payload: Mapping[str, Any]) -> OutcomeRun:
    """The job_queue handler (kind ledger.ARSENAL_OUTCOME_KIND). The payload
    is informational; the run always checks whatever is unchecked."""
    return check_unchecked()
