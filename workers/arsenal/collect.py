"""arsenal_pool_collect: read an ended pool batch and insert its cards.

Every 10 minutes. With no open batch it is one small read. When the batch
has ended: usage is recorded per result at the batch rate (core/ai.py
batch_results), each card is validated (cards.py), embedded (title and
work), checked for near-duplicates inside Postgres and inserted pending; a
UNIQUE or near-duplicate skip is counted and logged, never an error. One
arsenal_verify job is queued per new item, and the 'collected' row records
the counts and the cost.

The 'collected' row is always written once results were read, even when
embedding fails (the cards then go in without the near-duplicate check, and
the note says so): re-reading a batch would record its usage twice.

insert_candidates is shared with the drop's synchronous top-up (pool.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Sequence

from arsenal import cards
from core import ai, ledger
from core import embed as embed_mod

log = logging.getLogger("valemont.arsenal.collect")

PURPOSE = "arsenal_pool"
#: Batches end within 24 h; past this, the batch is written off as failed.
GIVE_UP_AFTER = timedelta(hours=26)
#: Cosine at or above this to a real reference is a near-duplicate
#: (docs/arsenal.md §7: a starting value, to calibrate).
NEAR_DUP = 0.92

Embed = Callable[[Sequence[str]], list[list[float]]]


@dataclass(frozen=True)
class Nearest:
    """One candidate's nearest real reference (owner, 2026-10-16: every
    near-duplicate skip is logged with its cosine and the matched key, and
    the first pool's distribution is reported before 0.92 is tuned)."""
    key: str
    title: str
    matched: str | None
    cosine: float | None
    skipped: bool


@dataclass
class Inserted:
    ref_ids: list[int] = field(default_factory=list)
    word_ids: list[int] = field(default_factory=list)
    skipped_dup: int = 0
    skipped_invalid: int = 0
    notes: list[str] = field(default_factory=list)
    #: Every embedded candidate, skipped or not.
    nearest: list[Nearest] = field(default_factory=list)

    @property
    def near_dup_skips(self) -> list[Nearest]:
        return [n for n in self.nearest if n.skipped]


def quantile(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, round(q * (len(s) - 1))))]


def distribution_note(ins: Inserted) -> str | None:
    """'nearest cosine n=30 min .41 p50 .63 p90 .82 max .95; near-dup skips: a~b .95, …'"""
    scores = [n.cosine for n in ins.nearest if n.cosine is not None]
    if not scores:
        return None
    note = (f"nearest cosine n={len(scores)} min {min(scores):.2f} p50 {quantile(scores, .5):.2f} "
            f"p90 {quantile(scores, .9):.2f} max {max(scores):.2f}")
    skips = ins.near_dup_skips
    if skips:
        note += "; near-dup skips: " + ", ".join(f"{n.key}~{n.matched} {n.cosine:.3f}" for n in skips)
    return note


def insert_candidates(raw_refs: Sequence[Any], raw_words: Sequence[Any], *, run_id: int | None,
                      blocked_keys: set[str], blocked_lemmas: set[str], embed: Embed | None = None) -> Inserted:
    """Validate, embed, near-duplicate check and insert; nothing raises for a
    bad or repeated card."""
    out = Inserted()
    good: list[ledger.ArsenalCardIn] = []
    seen = set(blocked_keys)
    for item in raw_refs:
        card, why = cards.validate_ref(item, seen)
        if card is None:
            out.skipped_invalid += 1
            log.info("arsenal candidate refused: %s", why)
            continue
        seen.add(card.canonical_key)
        good.append(card)
    vectors: list[list[float] | None] = [None] * len(good)
    if good:
        try:
            vectors = list((embed or embed_mod.embed)([cards.embed_text(c) for c in good]))
        except Exception as exc:
            out.notes.append(f"embed failed ({type(exc).__name__}): near-duplicate check skipped for {len(good)}")
            log.error("arsenal: embedding failed, inserting without the near-duplicate check: %s", exc)
    for card, vec in zip(good, vectors):
        if vec is not None:
            near = ledger.arsenal_nearest(vec)
            matched, cosine = near if near else (None, None)
            skip = cosine is not None and cosine >= NEAR_DUP
            out.nearest.append(Nearest(card.canonical_key, card.title, matched, cosine, skip))
            if skip:
                out.skipped_dup += 1
                log.info("arsenal candidate %s (%r) skipped: near-duplicate of %s, cosine %.4f",
                         card.canonical_key, card.title, matched, cosine)
                continue
            if cosine is not None:
                log.info("arsenal candidate %s nearest %s, cosine %.4f", card.canonical_key, matched, cosine)
        new_id = ledger.arsenal_insert_ref(card, pool_run_id=run_id, vector=vec)
        if new_id is None:
            out.skipped_dup += 1
            log.info("arsenal candidate %s skipped: the key exists", card.canonical_key)
            continue
        out.ref_ids.append(new_id)
    lemmas = set(blocked_lemmas)
    for item in raw_words:
        word, why = cards.validate_word(item, lemmas)
        if word is None:
            out.skipped_invalid += 1
            log.info("arsenal word refused: %s", why)
            continue
        lemmas.add(word.lemma)
        new_id = ledger.arsenal_insert_word(word, pool_run_id=run_id)
        if new_id is None:
            out.skipped_dup += 1
            log.info("arsenal word %r skipped: it exists", word.lemma)
            continue
        out.word_ids.append(new_id)
    return out


def queue_verification(ins: Inserted) -> None:
    for rid in ins.ref_ids:
        ledger.arsenal_queue_verify("ref", rid)
    for wid in ins.word_ids:
        ledger.arsenal_queue_verify("word", wid)


def blocked() -> tuple[set[str], set[str]]:
    """Keys and lemmas a candidate may never take: the seeds (references and
    the owner's known words), and every recent one."""
    keys = {k for k, _, _ in ledger.arsenal_seeds()} | set(ledger.arsenal_recent_keys(500))
    return keys, set(ledger.arsenal_seed_lemmas()) | set(ledger.arsenal_recent_lemmas(500))


@dataclass(frozen=True)
class CollectRun:
    job_note: str
    run_id: int | None = None
    inserted: Inserted | None = None


def collect(open_batch: ledger.ArsenalOpenBatch, *, client: Any = None, embed: Embed | None = None,
            queue: bool = True) -> CollectRun:
    """Read one ended batch and insert its cards, under the run's advisory
    lock and only while it is still uncollected (the worker's job and the
    --now script can race). `queue=False` leaves the verification to the
    caller (the --now script verifies inline)."""
    with ledger.arsenal_collect_lock(open_batch.run_id) as got:
        if not got:
            return CollectRun(f"batch {open_batch.batch_id}: another collector is reading it")
        current = ledger.arsenal_open_batch()
        if current is None or current.run_id != open_batch.run_id:
            return CollectRun(f"batch {open_batch.batch_id}: already collected")
        return _collect(open_batch, client=client, embed=embed, queue=queue)


def _collect(open_batch: ledger.ArsenalOpenBatch, *, client: Any, embed: Embed | None, queue: bool) -> CollectRun:
    cost = Decimal(0)

    def add_cost(_usage_id: int, _model: str, c: Decimal) -> None:
        nonlocal cost
        cost += c

    results = ai.batch_results(open_batch.batch_id, purpose=PURPOSE, model=open_batch.model or "claude-sonnet-5-5",
                               client=client, on_recorded=add_cost)
    raw_refs: list[Any] = []
    raw_words: list[Any] = []
    errors: list[str] = []
    for r in results:
        if r.message is None:
            errors.append(f"{r.custom_id} {r.kind}: {r.error}")
            continue
        data = cards.response_json(r.message)
        if not isinstance(data, dict):
            errors.append(f"{r.custom_id}: no JSON")
            continue
        if r.custom_id == "refs":
            raw_refs += data.get("refs") or []
        elif r.custom_id == "words":
            raw_words += data.get("words") or []
    keys, lemmas = blocked()
    ins = insert_candidates(raw_refs, raw_words, run_id=open_batch.run_id, blocked_keys=keys,
                            blocked_lemmas=lemmas, embed=embed)
    dist = distribution_note(ins)
    note = "; ".join(errors + ins.notes + ([dist] if dist else [])) or None
    ledger.arsenal_log_run("batch", "collected", run_id=open_batch.run_id, batch_id=open_batch.batch_id,
                           model=open_batch.model or None, cost_usd=cost, note=note,
                           inserted_refs=len(ins.ref_ids), inserted_words=len(ins.word_ids),
                           skipped_dup=ins.skipped_dup, skipped_invalid=ins.skipped_invalid)
    if queue:
        queue_verification(ins)
    summary = (f"collected batch {open_batch.batch_id}: {len(ins.ref_ids)} refs, {len(ins.word_ids)} words, "
               f"{ins.skipped_dup} duplicate, {ins.skipped_invalid} invalid, ${cost}")
    log.info("arsenal %s%s", summary, f" ({note})" if note else "")
    return CollectRun(summary, open_batch.run_id, ins)


def collect_job(*, client: Any = None, now: datetime | None = None) -> CollectRun:
    open_batch = ledger.arsenal_open_batch()
    if open_batch is None:
        return CollectRun("no open batch")
    status = ai.batch_status(open_batch.batch_id, client=client)
    if status != "ended":
        age = (now or datetime.now(timezone.utc)) - open_batch.submitted_at
        if age > GIVE_UP_AFTER:
            ledger.arsenal_log_run("batch", "failed", run_id=open_batch.run_id, batch_id=open_batch.batch_id,
                                   note=f"still {status} after {age}; written off")
            raise RuntimeError(f"arsenal batch {open_batch.batch_id} still {status} after {age}; written off")
        return CollectRun(f"batch {open_batch.batch_id} {status}")
    return collect(open_batch, client=client)
