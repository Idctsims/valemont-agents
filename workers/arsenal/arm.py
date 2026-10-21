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
from core import ledger

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
