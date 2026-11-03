"""Verify on save: an Arm Me card the owner saved (docs/arsenal.md §9.5).

Arm Me is fast first: its three cards stream straight from the model and are
stored 'arm' / 'pending' (db/059 arsenal_arm_card). Saving one is what
checks it. The save's trigger queues ONE job_queue row of kind
ledger.ARSENAL_ARM_VERIFY_KIND ({'ref_id': n}) per reference, ever, and
this handler runs it through the pool's own machinery, unchanged:

  1. verify: verify.verify_item (the same retrieval, judge and coverage
     guard as a pool card), when the card is still pending;
  2. the outcome check, when it settled verified or corrected and has no
     check on record: the three controls first, then the card's 3-read
     vote, recorded by outcome.record (a conflict also holds it). A control
     that comes out wrong raises ControlsFailed: nothing is recorded and the
     queue retries.

Each step is skipped when it is already done, so a retry resumes where the
last attempt stopped. A TEST card (e2e) is skipped with no AI call at all.
My Arsenal reads the result: Checked, Corrected, or Unverified with its
reason (and a held conflict shows as Unverified, with the outcome reason).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from arsenal import outcome, sources, verify
from core import ai, ledger

log = logging.getLogger("valemont.arsenal.arm")


@dataclass(frozen=True)
class ArmVerifyRun:
    job_note: str


def verify_saved(ref_id: int, *, client: Any = None, http: sources.Http | None = None,
                 ask: verify.Ask | None = None, ask_outcome: verify.AskOutcome | None = None,
                 samples: int = verify.OUTCOME_SAMPLES) -> str:
    """Verify, then outcome-check, one saved Arm Me card. Returns what happened."""
    state = ledger.arsenal_arm_state(ref_id)
    if state is None:
        return "skipped: no such reference"
    if state.status != "arm":
        return f"skipped: a {state.status} reference, not an Arm Me card"
    if state.is_test:
        return "skipped: test reference"
    steps: list[str] = []
    if state.verification == "pending":
        steps.append(f"verify {verify.verify_item('ref', ref_id, client=client, http=http, ask=ask)}")
        state = ledger.arsenal_arm_state(ref_id)
        if state is None:
            raise RuntimeError(f"arsenal arm verify: reference {ref_id} vanished after settling")
    if state.verification not in ("verified", "corrected"):
        steps.append(f"{state.verification}: no outcome check")
        return "; ".join(steps)
    if state.checked:
        steps.append("outcome check already on record")
        return "; ".join(steps)
    ref = ledger.arsenal_ref_to_verify(ref_id)
    if ref is None:
        raise RuntimeError(f"arsenal arm verify: reference {ref_id} vanished before its outcome check")
    ask_o = ask_outcome or (lambda r, e: verify.ask_outcome_haiku(r, e, client=client))
    controls = outcome.controls_gate(ask=ask_o, samples=samples, http=http)
    res = verify.outcome_check(ref, ask=ask_o, http=http, samples=samples)
    hold_id = outcome.record(ref_id, res, controls=controls, source=outcome.SOURCE)
    log.info("arsenal arm verify: %d %r: outcome %s votes %s; page %s; %s%s", ref_id, ref.title,
             "CONFLICT" if res.conflict else "ok", outcome.votes_text(res.votes) or "-", res.page, res.note,
             f" (held: arsenal_holds {hold_id})" if hold_id is not None else "")
    steps.append(f"outcome {'conflict, held' if res.conflict else 'ok'} "
                 f"({outcome.votes_text(res.votes) or '-'}; controls {controls})")
    return "; ".join(steps)


def ref_of(payload: Mapping[str, Any]) -> int:
    if "ref_id" not in payload:
        raise ValueError(f"arsenal_arm_verify payload names no ref_id: {sorted(payload)}")
    return int(payload["ref_id"])


def arm_verify_job(payload: Mapping[str, Any]) -> ArmVerifyRun:
    """The job_queue handler (kind ledger.ARSENAL_ARM_VERIFY_KIND)."""
    ref_id = ref_of(payload)
    return ArmVerifyRun(f"ref {ref_id}: {verify_saved(ref_id)}")


# ------------------------------------------------------------ stranded saves

SWEEP_JOB = "arsenal_arm_sweep"
SWEEP_S = 6 * 60 * 60
#: At most this many cards a run: each is up to ~10 Haiku calls.
SWEEP_LIMIT = 5
#: A save gets the queue's own chance first.
SWEEP_MIN_AGE_H = 1


class SweepFailed(RuntimeError):
    """One or more stranded cards failed again; the rest were still swept."""


def sweep_job(*, verify_one: Any = None) -> ArmVerifyRun:
    """STRANDED SAVES (owner, 2026-11-03): a saved Arm Me card whose one
    arsenal_arm_verify job failed for good stays pending ("Checking…")
    forever, since db/059 queues one per card, ever. Every 6 h this finishes
    them directly, oldest save first, through verify_saved (each step skips
    what is done). One card failing never stops the next; any failure raises
    at the end so job_health shows it. The Anthropic credit error stops the
    run at once: every card would fail the same way (core/ai.py)."""
    run = verify_one or verify_saved
    ids = ledger.arsenal_stranded_saves(SWEEP_LIMIT, SWEEP_MIN_AGE_H)
    if not ids:
        return ArmVerifyRun("no stranded saves")
    done: list[str] = []
    failed: list[str] = []
    for ref_id in ids:
        try:
            done.append(f"ref {ref_id}: {run(ref_id)}")
        except Exception as exc:
            if ai.credit_cause(exc) is not None:
                raise
            log.error("arsenal arm sweep: ref %d failed: %s: %s", ref_id, type(exc).__name__, exc)
            failed.append(f"ref {ref_id}: {type(exc).__name__}: {exc}")
    log.info("arsenal arm sweep: %d finished, %d failed: %s", len(done), len(failed), "; ".join(done + failed))
    if failed:
        raise SweepFailed(f"{len(failed)} of {len(ids)} stranded save(s) failed: " + "; ".join(failed))
    return ArmVerifyRun(f"{len(done)} stranded save(s) finished: " + "; ".join(done))
