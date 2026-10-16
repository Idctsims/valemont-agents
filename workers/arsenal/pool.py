"""arsenal_pool: the weekly Message Batch, and the drop's synchronous top-up.

Sundays 03:30 Chicago, and at boot. Each run first embeds any real
reference that has no embedding yet (the seeds, at first: the
near-duplicate check must see them) and queues verification again for
anything pending over a day with no job. Then it SUBMITS (should_submit)
when no batch is open and either the last submission is 6+ days old (the
Sunday run) or the pool is short: under 21 references or 7 words, counting
verified, corrected and pending.

One batch, two requests (Sonnet, non-critical; the 80% budget downgrade
applies): POOL_REFS references split across the categories by the current
weights, and POOL_WORDS Power Words. The system blocks carry the voice, the
rules, and the seeds and recent keys (cached).

The top-up (drop.py, when today's pick would be short): ONE synchronous
Sonnet call for TOPUP_REFS references in the thinnest categories and
TOPUP_WORDS words, inserted and verified inline by the same code path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Sequence

from arsenal import cards, collect, verify
from core import ai, ledger
from core import embed as embed_mod

log = logging.getLogger("valemont.arsenal.pool")

MODEL = "claude-sonnet-5-5"
PURPOSE_TOPUP = "arsenal_topup"
RESUBMIT_AFTER = timedelta(days=6)
#: Seven days of drops.
MIN_REFS = 21
MIN_WORDS = 7
REFS_MAX_TOKENS = 12_000
WORDS_MAX_TOKENS = 3_000
TOPUP_MAX_TOKENS = 5_000


def context() -> cards.PromptContext:
    return cards.PromptContext(seeds=ledger.arsenal_seeds(), recent_keys=ledger.arsenal_recent_keys(60),
                               recent_lemmas=ledger.arsenal_recent_lemmas(60))


def should_submit(status: ledger.ArsenalPoolStatus, now: datetime) -> tuple[bool, str]:
    """(submit?, why). Pure."""
    if status.open_batch is not None:
        return False, f"batch {status.open_batch.batch_id} is open"
    refs = status.refs_ready + status.refs_pending
    words = status.words_ready + status.words_pending
    if refs < MIN_REFS or words < MIN_WORDS:
        return True, f"pool short: {refs} refs, {words} words (verified or pending)"
    if status.last_submitted_at is None or now - status.last_submitted_at >= RESUBMIT_AFTER:
        return True, "weekly batch due"
    return False, f"pool fine: {refs} refs, {words} words; last batch {(now - status.last_submitted_at).days} d ago"


def batch_requests(ctx: cards.PromptContext, split: dict[str, int], words: int) -> list[ai.BatchRequest]:
    system = cards.system_blocks(ctx)

    def params(message: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"system": system, "messages": [{"role": "user", "content": message}],
                "thinking": {"type": "between_tools"},
                "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": schema}}}

    return [ai.BatchRequest("refs", params(cards.refs_message(split), cards.refs_schema()), REFS_MAX_TOKENS),
            ai.BatchRequest("words", params(cards.words_message(words), cards.words_schema()), WORDS_MAX_TOKENS)]


def submit(*, client: Any = None) -> tuple[int, str]:
    """Submit the weekly batch and log it. Returns (run id, batch id)."""
    split = cards.allocate(cards.POOL_REFS, cards.category_weights(ledger.arsenal_weights()))
    batch_id, model = ai.submit_batch(requests=batch_requests(context(), split, cards.POOL_WORDS), model=MODEL,
                                      client=client)
    run_id = ledger.arsenal_log_run("batch", "submitted", batch_id=batch_id, model=model,
                                    refs_requested=sum(split.values()), words_requested=cards.POOL_WORDS)
    log.info("arsenal pool: batch %s submitted (run %d): %s", batch_id, run_id, split)
    return run_id, batch_id


def embed_missing(*, embed: collect.Embed | None = None) -> int:
    """Embed real references that have none (the seeds, at first)."""
    rows = ledger.arsenal_unembedded()
    if not rows:
        return 0
    vectors = (embed or embed_mod.embed)([f"{title} — {work}" for _, title, work in rows])
    for (ref_id, _, _), vec in zip(rows, vectors):
        ledger.arsenal_set_embedding(ref_id, vec)
    return len(rows)


def requeue_stale() -> int:
    stale = ledger.arsenal_stale_pending(24)
    for target, item_id in stale:
        ledger.arsenal_queue_verify(target, item_id)
    return len(stale)


@dataclass(frozen=True)
class PoolRun:
    job_note: str


def pool_job(*, client: Any = None, now: datetime | None = None, embed: collect.Embed | None = None) -> PoolRun:
    notes = []
    try:
        n = embed_missing(embed=embed)
        if n:
            notes.append(f"embedded {n}")
    except embed_mod.EmbedError as exc:
        # The near-duplicate check misses these until the next run; the pool still runs.
        notes.append(f"embed failed: {exc}")
        log.error("arsenal pool: embedding the unembedded failed: %s", exc)
    requeued = requeue_stale()
    if requeued:
        notes.append(f"requeued {requeued} pending")
    status = ledger.arsenal_pool_status()
    go, why = should_submit(status, now or datetime.now(timezone.utc))
    notes.append(why)
    if go:
        _, batch_id = submit(client=client)
        notes.append(f"submitted {batch_id}")
    return PoolRun("; ".join(notes))


# ------------------------------------------------------------------ top-up

def topup_split(ready: Sequence[str], total: int = cards.TOPUP_REFS) -> dict[str, int]:
    """`total` references, first in the categories with nothing ready (in
    CATEGORIES order, the down-weighted last), two each, then round-robin."""
    order = [c for c in cards.CATEGORIES if c not in ready] + [c for c in cards.CATEGORIES if c in ready]
    out: dict[str, int] = {}
    i = 0
    while sum(out.values()) < total:
        c = order[i % len(order)]
        out[c] = out.get(c, 0) + min(2, total - sum(out.values()))
        i += 1
    return out


@dataclass(frozen=True)
class TopUp:
    run_id: int
    refs: int
    words: int
    cost: Decimal
    verified: dict[str, int]


def topup(status: ledger.ArsenalPoolStatus, *, client: Any = None, http: Any = None,
          ask: verify.Ask | None = None, embed: collect.Embed | None = None) -> TopUp:
    """One synchronous Sonnet call, inserted and verified inline."""
    split = topup_split(status.ready_categories)
    words = cards.TOPUP_WORDS
    ctx = context()
    run_id = ledger.arsenal_log_run("topup", "submitted", model=MODEL, refs_requested=sum(split.values()),
                                    words_requested=words)
    cost = Decimal(0)

    def add_cost(_usage_id: int, _model: str, c: Decimal) -> None:
        nonlocal cost
        cost += c

    response = ai.complete(
        purpose=PURPOSE_TOPUP, model=MODEL, critical=False, max_tokens=TOPUP_MAX_TOKENS, client=client,
        system=cards.system_blocks(ctx),
        messages=[{"role": "user", "content": cards.refs_message(split) + " " + cards.words_message(words)}],
        thinking={"type": "between_tools"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": cards.topup_schema()}},
        on_recorded=add_cost,
    )
    data = cards.response_json(response)
    data = data if isinstance(data, dict) else {}
    keys, lemmas = collect.blocked()
    ins = collect.insert_candidates(data.get("refs") or [], data.get("words") or [], run_id=run_id,
                                    blocked_keys=keys, blocked_lemmas=lemmas, embed=embed)
    dist = collect.distribution_note(ins)
    ledger.arsenal_log_run("topup", "collected", run_id=run_id, model=MODEL, cost_usd=cost,
                           note="; ".join(ins.notes + ([dist] if dist else [])) or None, inserted_refs=len(ins.ref_ids),
                           inserted_words=len(ins.word_ids), skipped_dup=ins.skipped_dup,
                           skipped_invalid=ins.skipped_invalid)
    verified: dict[str, int] = {}
    for target, ids in (("ref", ins.ref_ids), ("word", ins.word_ids)):
        for item_id in ids:
            result = verify.verify_item(target, item_id, client=client, http=http, ask=ask)
            verified[result] = verified.get(result, 0) + 1
    log.info("arsenal top-up (run %d): %d refs, %d words, $%s; %s", run_id, len(ins.ref_ids), len(ins.word_ids),
             cost, verified)
    return TopUp(run_id, len(ins.ref_ids), len(ins.word_ids), cost, verified)
