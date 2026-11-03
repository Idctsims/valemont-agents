"""The Screening Room hook: a finished title drafts its references into the
Arsenal (Chat 5 Phase 3; db/062; docs/arsenal.md §10).

db/062's trigger writes ONE arsenal_title_hooks row per title, ever, when it
is first marked finished, and queues ONE job_queue row of kind
ledger.ARSENAL_TITLE_HOOK_KIND ({'hook_id', 'title_id'}) for a real title. A
re-watch drafts nothing. This handler:

  1. DRAFTS (hook queued): one Sonnet call (non-critical; the 80% budget
     downgrade applies) with the Arsenal voice, the rules (cards.RULES: the
     outcome rule, the film/TV credit rule), the owner's canon and every
     reference ever proposed, the title (name, year, kind, overview, genres,
     TMDB keywords) and the owner's taste weights. It asks for 3 to 5
     references (mostly film_tv; a real-world history or business angle may
     land elsewhere) and 1 Power Word from the title's world. Each card is
     validated (cards.validate_ref), names the title as its source work
     (forced when the model's did not), carries a quote only credited to the
     character and the title, and passes the near-duplicate check. The cards,
     the word and the hook's one move (drafted, or failed when no card passed)
     are written in ONE transaction (ledger.arsenal_title_hook_draft).
  2. CHECKS (hook drafted): every card through the pool's own verifier
     (verify.verify_item), the word through kaikki, then the outcome check:
     the three controls once, then each checked card's 3-read vote, recorded
     by outcome.record (a conflict also holds it). Unverified cards stay in
     the pool, flagged, and never drop. Cleared cards are ordinary pool cards
     tagged to the title: they can drop later; the hook never forces a drop.

Every step skips what is done, so a retry resumes where the last stopped. A
control that comes out wrong raises ControlsFailed (nothing recorded, the
queue retries). The Anthropic credit error propagates: the queue defers the
job an hour without spending an attempt (core/ai.py, the #68 deferral). A
TEST hook is never queued by the database; if one reaches here it is skipped
with no AI call. A hand re-run (scripts.arsenal_title_hook --rerun) is the
only way a FAILED hook drafts again, and it records itself
(arsenal_title_hook_reruns, append-only).
"""

from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from arsenal import cards, collect, outcome, pool, sources, verify
from core import ai, ledger
from core import embed as embed_mod

log = logging.getLogger("valemont.arsenal.hook")

KIND = ledger.ARSENAL_TITLE_HOOK_KIND
MODEL = pool.MODEL
PURPOSE = "arsenal_title_hook"
#: 5 cards of ~180 tokens and a word, with medium-effort thinking.
MAX_TOKENS = 5_000
MIN_REFS = 3
MAX_REFS = 5
#: The overview is trimmed to this in the prompt.
OVERVIEW_CHARS = 1_200
KEYWORDS_SHOWN = 25

Embed = Callable[[Sequence[str]], list[list[float]]]


class HookError(RuntimeError):
    """A hook asked to do what db/062 never allows (re-running a drafted one)."""


# ------------------------------------------------------------------- prompt

HOOK_RULES = """\
THE TITLE THE OWNER JUST FINISHED
{title} ({year}), a {kind}.
Genres: {genres}
TMDB keywords: {keywords}
Overview: {overview}

THE OWNER'S TASTE (weights, 1.0 is neutral; higher means more of it)
Categories: {cat_weights}
Rooms: {room_weights}

WRITE
- {min_refs} to {max_refs} references drawn from THIS title, best first, and exactly 1 Power Word from its world.
- source_work names this title on every reference: "{work}". Never another work.
- Mostly film_tv: a character's move, a scene, a strategy the story turns on. A real-world angle may land in another category (a show about a media dynasty can give a boardroom move from the business it mirrors); its source_work still names this title.
- Mostly the move described in your own words. A quote only when you are certain of the exact words: at most 15 words, quote_by credits the character AND the title, "Logan Roy (as written in Succession)".
- source_page: this title's English Wikipedia page ("{page_hint}"), or its Wikiquote page when the card carries a quote.
- Check the ending (the rule above): a move that failed in the story is never a line about winning.
- The Power Word: a word this title's world uses that a sharp GP would say unprompted. Not trivia, not a character's name.
"""


def _kind_word(kind: str) -> str:
    return "series" if kind == "tv" else "film"


def work_of(title: ledger.TitleForHook) -> str:
    """The source_work every card names: "Succession (2018)"."""
    return f"{title.name} ({title.year})" if title.year else title.name


def _weights_line(weights: Mapping[tuple[str, str], float], dim: str) -> str:
    items = sorted(((k, w) for (d, k), w in weights.items() if d == dim), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{k} {w:.2f}" for k, w in items) or "(none yet)"


def hook_message(title: ledger.TitleForHook, weights: Mapping[tuple[str, str], float]) -> str:
    overview = " ".join((title.overview or "").split())
    if len(overview) > OVERVIEW_CHARS:
        overview = overview[:OVERVIEW_CHARS].rsplit(" ", 1)[0] + "…"
    page = f"{title.name} (TV series)" if title.kind == "tv" else f"{title.name} (film)"
    return HOOK_RULES.format(
        title=title.name, year=title.year or "year unknown", kind=_kind_word(title.kind),
        genres=", ".join(title.genres) or "(none)",
        keywords=", ".join(title.keywords[:KEYWORDS_SHOWN]) or "(none)", overview=overview or "(none)",
        cat_weights=_weights_line(weights, "category"), room_weights=_weights_line(weights, "room"),
        min_refs=MIN_REFS, max_refs=MAX_REFS, work=work_of(title), page_hint=page)


def system_blocks(ctx: cards.PromptContext) -> list[dict[str, Any]]:
    """The pool's blocks (voice, rules, canon, every reference ever proposed),
    uncached: the hook is sporadic, and a cache write costs 1.25x."""
    return [{k: v for k, v in b.items() if k != "cache_control"} for b in cards.system_blocks(ctx)]


def hook_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": ["refs", "word"],
            "properties": {"refs": {"type": "array", "items": cards.ref_item_schema()},
                           "word": cards.word_item_schema()}}


# --------------------------------------------------------------- validation

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def names_title(source_work: str, title: ledger.TitleForHook) -> bool:
    return _norm(title.name) in _norm(source_work)


def fit_card(card: ledger.ArsenalCardIn, title: ledger.TitleForHook) -> tuple[ledger.ArsenalCardIn, list[str]]:
    """The owner's rules on a validated card (Oct 15): it names the title as
    its source work (forced to "<Title> (<year>)" when it does not), and any
    quote credits the character AND the title, in every category (a
    boardroom card from a show still quotes the show); a quote that cannot be
    credited goes, and the card stays a described move."""
    notes: list[str] = []
    work = card.source_work
    if not names_title(work, title):
        notes.append(f"{card.canonical_key}: source_work {work!r} -> {work_of(title)!r}")
        work = work_of(title)
    quote, quote_by = card.quote, card.quote_by
    if quote:
        quote_by = cards.credit_film_quote(quote_by, title.name)
        if quote_by is None:
            notes.append(f"{card.canonical_key}: quote dropped (no speaker to credit)")
            quote = None
    return dataclasses.replace(card, source_work=work, quote=quote, quote_by=quote_by), notes


@dataclass
class Screened:
    refs: list[tuple[ledger.ArsenalCardIn, list[float] | None]] = dataclasses.field(default_factory=list)
    word: ledger.ArsenalWordIn | None = None
    invalid: int = 0
    near_dup: int = 0
    notes: list[str] = dataclasses.field(default_factory=list)


def screen(data: Any, title: ledger.TitleForHook, *, blocked_keys: set[str], blocked_lemmas: set[str],
           embed: Embed | None = None) -> Screened:
    """Validate, fit, embed and near-duplicate check the answer; nothing
    raises for a bad card. At most MAX_REFS kept, in the model's order."""
    out = Screened()
    data = data if isinstance(data, Mapping) else {}
    seen = set(blocked_keys)
    good: list[ledger.ArsenalCardIn] = []
    for item in data.get("refs") or []:
        card, why = cards.validate_ref(item, seen)
        if card is None:
            out.invalid += 1
            log.info("arsenal hook: card refused: %s", why)
            continue
        if len(good) >= MAX_REFS:
            out.notes.append(f"{card.canonical_key}: past {MAX_REFS}, left out")
            continue
        card, notes = fit_card(card, title)
        out.notes.extend(notes)
        seen.add(card.canonical_key)
        good.append(card)
    vectors: list[list[float] | None] = [None] * len(good)
    if good:
        try:
            vectors = list((embed or embed_mod.embed)([cards.embed_text(c) for c in good]))
        except Exception as exc:
            out.notes.append(f"embed failed ({type(exc).__name__}): near-duplicate check skipped")
            log.error("arsenal hook: embedding failed, writing without the near-duplicate check: %s", exc)
    for card, vec in zip(good, vectors):
        if vec is not None:
            near = ledger.arsenal_nearest(vec)
            if near and near[1] is not None and near[1] >= collect.NEAR_DUP:
                out.near_dup += 1
                out.notes.append(f"{card.canonical_key}~{near[0]} {near[1]:.3f} (near-duplicate)")
                continue
        out.refs.append((card, vec))
    word, why = cards.validate_word(data.get("word"), set(blocked_lemmas))
    if word is None:
        out.notes.append(f"word refused: {why}")
    out.word = word
    return out


# -------------------------------------------------------------------- steps

@dataclass(frozen=True)
class HookRun:
    job_note: str


def draft(hook: ledger.ArsenalTitleHook, *, client: Any = None, embed: Embed | None = None,
          rerun_by: str | None = None) -> ledger.HookDraft | None:
    """The Sonnet call and the one-transaction write. None when the title
    is gone (the hook is failed with that reason)."""
    title = ledger.arsenal_title_for_hook(hook.title_id)
    if title is None:
        ledger.arsenal_title_hook_fail(hook.id, f"title {hook.title_id} not found", rerun_by=rerun_by)
        return None
    ctx = pool.context()
    usage: dict[str, Any] = {}

    def recorded(usage_id: int, model: str, cost: Any) -> None:
        usage.update(id=usage_id, model=model, cost=cost)

    response = ai.complete(
        purpose=PURPOSE, model=MODEL, critical=False, max_tokens=MAX_TOKENS, client=client,
        system=system_blocks(ctx),
        messages=[{"role": "user", "content": hook_message(title, ledger.arsenal_weights())}],
        thinking={"type": "between_tools"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": hook_schema()}},
        on_recorded=recorded,
    )
    data = cards.response_json(response)
    if data is None:
        ledger.arsenal_title_hook_fail(hook.id, "the model refused or answered no JSON", ai_usage_id=usage.get("id"),
                                       rerun_by=rerun_by)
        log.warning("arsenal hook %d (%s): no JSON from the model", hook.id, title.name)
        return ledger.HookDraft("failed", (), None, 0)
    keys, lemmas = collect.blocked()
    s = screen(data, title, blocked_keys=keys, blocked_lemmas=lemmas, embed=embed)
    head = f"{len(s.refs)} card(s) passed, {s.invalid} invalid, {s.near_dup} near-duplicate"
    note = "; ".join([head, *s.notes])
    if not s.refs:
        ledger.arsenal_title_hook_fail(hook.id, f"no card passed: {note}", ai_usage_id=usage.get("id"),
                                       rerun_by=rerun_by)
        log.warning("arsenal hook %d (%s): no card passed: %s", hook.id, title.name, note)
        return ledger.HookDraft("failed", (), None, 0)
    out = ledger.arsenal_title_hook_draft(hook.id, title.id, s.refs, s.word, ai_usage_id=usage.get("id"),
                                          note=note, rerun_by=rerun_by)
    log.info("arsenal hook %d (%s): %s, refs %s, word %s, $%s; %s", hook.id, title.name, out.status,
             list(out.ref_ids), out.word_id, usage.get("cost"), note)
    return out


def check(ref_ids: Sequence[int], word_id: int | None, *, client: Any = None, http: sources.Http | None = None,
          ask: verify.Ask | None = None, ask_outcome: verify.AskOutcome | None = None,
          samples: int = verify.OUTCOME_SAMPLES) -> str:
    """Verify every pending card and the word, then outcome-check every
    checked card with no check on record (controls once, first). Each step
    skips what is done."""
    tally: dict[str, int] = {}
    for rid in ref_ids:
        result = verify.verify_item("ref", rid, client=client, http=http, ask=ask)
        tally[result] = tally.get(result, 0) + 1
    word = verify.verify_item("word", word_id, client=client, http=http) if word_id is not None else "no word"
    due = []
    for rid in ref_ids:
        st = ledger.arsenal_arm_state(rid)
        if st is not None and st.verification in ("verified", "corrected") and not st.checked:
            ref = ledger.arsenal_ref_to_verify(rid)
            if ref is not None:
                due.append(ref)
    parts = [f"verify {', '.join(f'{k} {v}' for k, v in sorted(tally.items())) or 'none'}", f"word {word}"]
    if not due:
        parts.append("outcome: nothing to check")
        return "; ".join(parts)
    ask_o = ask_outcome or (lambda r, e: verify.ask_outcome_haiku(r, e, client=client))
    controls = outcome.controls_gate(ask=ask_o, samples=samples, http=http)
    held = 0
    for ref in due:
        res = verify.outcome_check(ref, ask=ask_o, http=http, samples=samples)
        hold_id = outcome.record(ref.id, res, controls=controls, source=outcome.SOURCE)
        held += hold_id is not None
        log.info("arsenal hook outcome: %d %r: %s votes %s; page %s; %s", ref.id, ref.title,
                 "CONFLICT" if res.conflict else "ok", outcome.votes_text(res.votes) or "-", res.page, res.note)
    parts.append(f"outcome checked {len(due)}, held {held} (controls {controls})")
    return "; ".join(parts)


def run_hook(hook_id: int, *, client: Any = None, http: sources.Http | None = None, ask: verify.Ask | None = None,
             ask_outcome: verify.AskOutcome | None = None, embed: Embed | None = None,
             samples: int = verify.OUTCOME_SAMPLES, rerun_by: str | None = None) -> str:
    """Draft (if queued, or failed and re-run by hand), then check. Returns
    what happened."""
    hook = ledger.arsenal_title_hook(hook_id)
    if hook is None:
        return "skipped: no such hook"
    if hook.is_test:
        return "skipped: test title"
    if rerun_by is not None and hook.status != "failed":
        raise HookError(f"hook {hook_id} is {hook.status}: only a failed hook is re-run by hand (once per title)")
    steps: list[str] = []
    if hook.status == "failed" and rerun_by is None:
        return f"skipped: failed ({hook.note or 'no note'}); a hand re-run drafts it again"
    if hook.status in ("queued", "failed"):
        d = draft(hook, client=client, embed=embed, rerun_by=rerun_by)
        if d is None or d.status != "drafted":
            fresh = ledger.arsenal_title_hook(hook_id)
            return f"failed: {fresh.note if fresh else 'no hook'}"
        steps.append(f"drafted {len(d.ref_ids)} card(s)" + (", a word" if d.word_id else ", no word"))
        ref_ids, word_id = d.ref_ids, d.word_id
    else:
        ref_ids, word_id = hook.ref_ids, hook.word_id
    steps.append(check(ref_ids, word_id, client=client, http=http, ask=ask, ask_outcome=ask_outcome, samples=samples))
    return "; ".join(steps)


def hook_of(payload: Mapping[str, Any]) -> int:
    if "hook_id" not in payload:
        raise ValueError(f"arsenal_title_hook payload names no hook_id: {sorted(payload)}")
    return int(payload["hook_id"])


def hook_job(payload: Mapping[str, Any]) -> HookRun:
    """The job_queue handler (kind ledger.ARSENAL_TITLE_HOOK_KIND)."""
    hook_id = hook_of(payload)
    return HookRun(f"hook {hook_id}: {run_hook(hook_id)}")


# ------------------------------------------------------------ stranded hooks

SWEEP_JOB = "arsenal_title_hook_sweep"
SWEEP_S = 6 * 60 * 60
SWEEP_LIMIT = 3
#: The queue's own chance first.
SWEEP_MIN_AGE_H = 1


class SweepFailed(RuntimeError):
    """One or more stranded hooks failed again; the rest were still swept."""


def sweep_job(*, run_one: Callable[[int], str] | None = None) -> HookRun:
    """A hook whose one job failed for good stays "Drafting…" (or its cards
    "Checking…") forever. Every 6 h this finishes them directly, oldest
    first, through run_hook (each step skips what is done). One failing
    never stops the next; any failure raises at the end so job_health shows
    it. The Anthropic credit error stops the run at once (core/ai.py)."""
    run = run_one or run_hook
    ids = ledger.arsenal_stranded_title_hooks(SWEEP_LIMIT, SWEEP_MIN_AGE_H)
    if not ids:
        return HookRun("no stranded hooks")
    done: list[str] = []
    failed: list[str] = []
    for hook_id in ids:
        try:
            done.append(f"hook {hook_id}: {run(hook_id)}")
        except Exception as exc:
            if ai.credit_cause(exc) is not None:
                raise
            log.error("arsenal hook sweep: hook %d failed: %s: %s", hook_id, type(exc).__name__, exc)
            failed.append(f"hook {hook_id}: {type(exc).__name__}: {exc}")
    log.info("arsenal hook sweep: %d finished, %d failed: %s", len(done), len(failed), "; ".join(done + failed))
    if failed:
        raise SweepFailed(f"{len(failed)} of {len(ids)} stranded hook(s) failed: " + "; ".join(failed))
    return HookRun(f"{len(done)} stranded hook(s) finished: " + "; ".join(done))
