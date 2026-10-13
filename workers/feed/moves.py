"""Move detection (Chat 3 Phase 3; docs/sports_news.md §5).

The summary call returns `moves` per story: [{person, from, to, kind}], kind
trade | signing | release | retirement, "" for an unknown or absent name. A
move becomes an entity_link_proposals row (db/042) for the owner to decide on
/settings/feed ONLY when every rule holds; otherwise it is dropped, quietly
(logged), and nothing is proposed:

1. `person` resolves (exact name or alias, case-insensitive) to exactly one
   real PERSON entity that has a follow, of the story's sport when the story
   has one.
2. `to`, for a trade or a signing, resolves to exactly one team or tour of
   the person's sport; for a release or a retirement it is empty.
3. `from`, when given, resolves to the person's CURRENT link target.
4. The move changes something: `to` differs from the current target; a
   release or retirement needs a current link.
5. At most one open proposal per person (the database enforces it too).
6. No repeat of a (person, kind, to) the owner dismissed in the last
   DISMISS_QUIET_D days (owner D5).

Pure apart from the ledger reads and writes in propose().
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from core import ledger

log = logging.getLogger("valemont.feed.moves")

KINDS = ("trade", "signing", "release", "retirement")
MAX_MOVES = 3
MAX_NAME = 80
DISMISS_QUIET_D = 30
#: The link kind a person's sport implies, when the person has no current link.
LINK_KIND = {"nba": "plays_for", "nfl": "plays_for", "cfb": "plays_for", "f1": "drives_for", "golf": "member_of"}

_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class RawMove:
    person: str
    from_: str
    to: str
    kind: str


def parse(raw: Any) -> tuple[RawMove, ...]:
    """The summarizer's `moves`, cleaned: well-formed items only, at most
    MAX_MOVES. Never raises; a malformed list is no moves."""
    out: list[RawMove] = []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict) or m.get("kind") not in KINDS:
            continue
        person = _clean(m.get("person"))
        if not person:
            continue
        out.append(RawMove(person, _clean(m.get("from")), _clean(m.get("to")), m["kind"]))
    return tuple(out[:MAX_MOVES])


def _clean(v: Any) -> str:
    return _SPACE.sub(" ", str(v or "")).strip()[:MAX_NAME]


def norm(name: str) -> str:
    return _SPACE.sub(" ", name.lower()).strip()


def resolve(name: str, entities: Iterable[ledger.MoveEntity], kinds: Iterable[str],
            sport: str | None) -> ledger.MoveEntity | None:
    """Exactly one entity of these kinds (and this sport, when given) whose
    name or an alias equals `name`; None for none or several."""
    n = norm(name)
    if not n:
        return None
    ks = set(kinds)
    hits = {e.id: e for e in entities
            if e.kind in ks and (sport is None or e.sport == sport)
            and (norm(e.name) == n or n in e.aliases)}
    return next(iter(hits.values())) if len(hits) == 1 else None


@dataclass(frozen=True)
class Verdict:
    proposal: ledger.ProposalIn | None
    #: Why nothing was proposed (for the log), or "" for a proposal.
    reason: str = ""


def check(move: RawMove, story_id: int, story_sport: str | None, ctx: ledger.MoveContext) -> Verdict:
    """Rules 1-6 against the context read once per run."""
    person = resolve(move.person, ctx.entities, ("person",), story_sport)
    if person is None:
        return Verdict(None, f"person {move.person!r} does not resolve")
    if not person.followed:
        return Verdict(None, f"{person.name} is not followed")
    current = [l for l in ctx.links if l.from_id == person.id]
    link_kind = current[0].kind if current else LINK_KIND.get(person.sport or "", "plays_for")
    current = [l for l in current if l.kind == link_kind]
    if len(current) > 1:
        return Verdict(None, f"{person.name} has {len(current)} current {link_kind} links")
    cur_to = current[0].to_id if current else None

    to_id: int | None = None
    if move.kind in ("trade", "signing"):
        to = resolve(move.to, ctx.entities, ("team", "tour"), person.sport)
        if to is None:
            return Verdict(None, f"destination {move.to!r} does not resolve")
        to_id = to.id
        if to_id == cur_to:
            return Verdict(None, f"{person.name} is already with {to.name}")
    elif move.to:
        return Verdict(None, f"a {move.kind} has no destination")
    elif cur_to is None:
        return Verdict(None, f"a {move.kind} with no current link")

    if move.from_:
        frm = resolve(move.from_, ctx.entities, ("team", "tour"), person.sport)
        if frm is None or frm.id != cur_to:
            return Verdict(None, f"from {move.from_!r} is not {person.name}'s current link")
    if person.id in ctx.open_persons:
        return Verdict(None, f"{person.name} already has an open proposal")
    if (person.id, move.kind, to_id) in ctx.recent_dismissals:
        return Verdict(None, f"dismissed within {DISMISS_QUIET_D} days")
    return Verdict(ledger.ProposalIn(person.id, move.kind, link_kind, cur_to, to_id, story_id))


def propose(found: Sequence[tuple[int, str | None, Sequence[RawMove]]],
            ctx: ledger.MoveContext | None = None) -> int:
    """found: (story_id, the story's sport, its moves). Writes the proposals
    that pass; returns how many. Reads the context once."""
    if not any(moves for _, _, moves in found):
        return 0
    ctx = ctx or ledger.feed_move_context(DISMISS_QUIET_D)
    opened = set(ctx.open_persons)
    made = 0
    for story_id, sport, moves in found:
        for m in moves:
            v = check(m, story_id, sport, ledger.MoveContext(ctx.entities, ctx.links, frozenset(opened),
                                                             ctx.recent_dismissals))
            if v.proposal is None:
                log.info("feed moves: story %d: no proposal (%s)", story_id, v.reason)
                continue
            pid = ledger.feed_insert_proposal(v.proposal)
            if pid is None:
                log.info("feed moves: story %d: the database refused the proposal (open or stale)", story_id)
                continue
            opened.add(v.proposal.person_id)
            made += 1
            log.info("feed moves: proposal %d from story %d (%s)", pid, story_id, m.kind)
    return made
