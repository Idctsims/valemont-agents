"""Phase 4.3 dry run (owner's machine): one slot's tickets on the live board. Commits nothing.

    ..\\venv\\Scripts\\python.exe -m scripts.parlay_ticket_dryrun --env-file <repo>\\.env [--draws N]

The 4.2 path (the candidate reader through `valemont_readonly`, every
transaction rolled back; the strict re-quote, latest start first, at the
re-quote's own pace floor; rules 1-4 at the end of the re-quote), then the
4.3 search (consensus.parlay_ticket.slot): per tier and sport the ticket or
the empty slot with its binding rule, its prices, the relative cost over
fair, the leg reasons, and the Monte Carlo pricing time per ticket; the
cross-sport Lotto on its own line; the time per slot. The report is printed
and saved under research_data/parlays/ (gitignored). Nothing is written to
the database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from consensus import correlation as cr
from consensus import parlay_legs as pl
from consensus import parlay_prereg
from consensus import parlay_ticket as pt
from core import ledger
from core import preregistration as pr
from core.paths import REPO_ROOT
from scripts.parlay_dryrun import crawler_running, readonly
from scripts.research.kalshi_history import DATA_DIR


NORMALIZED: set[str] = set()
ENV_FILE: Path | None = None


def stamp_state() -> str:
    """Whether every v2 section, as it stands, has a stamp before now: read as
    valemont_readonly (never DATABASE_URL), every transaction rolled back. The
    dry run commits nothing either way; 4.4's adapter refuses without it."""
    doc = (REPO_ROOT / parlay_prereg.DOCUMENT).read_text(encoding="utf-8")
    missing = []
    with psycopg.connect(readonly(ENV_FILE)) as conn:  # type: ignore[arg-type]
        try:
            for s in parlay_prereg.SECTIONS:
                sha = pr.content_hash(pr.section_text(doc, s))
                got = ledger.preregistration_stamp(document=parlay_prereg.DOCUMENT, section=s, content_sha256=sha,
                                                   conn=conn)
                if got is None or not got[0] < got[1]:
                    missing.append(s.split(".")[0])
        finally:
            conn.rollback()
    return "v2 stamped (all 11 sections)" if not missing else f"NOT stamped: {', '.join(missing)}"


def legs_json(k: pt.Ticket, stored: dict) -> list[dict]:
    """Each leg: its side, ask and fresh side mid, the recomputed side p and the
    board's stored side p it replaced (the stale input the Nov 20 fix removes)."""
    def side(p, s):
        return None if p is None else (p if s == "yes" else 1 - p)

    return [{"ticker": c.m.market_ticker, "side": c.leg.side, "tier": c.m.tier, "ask": str(c.ask),
             "mid": str(pt.side_mid(c)), "p_fresh": str(c.p), "p_stored": str(side(stored.get(c.m.market_ticker), c.leg.side)),
             "stale_outside": c.m.stale_outside, "normalized": c.m.event_ticker in NORMALIZED} for c in k.legs]


def leg_mix(results: list[pt.Result]) -> dict[str, int]:
    """Across every ticket in the slot: legs, ESPN-tier legs, books-tier legs, and legs in a normalized event."""
    legs = [c for r in results if r.ticket for c in r.ticket.legs]
    return {"legs": len(legs), "espn_tier": sum(1 for c in legs if c.m.tier == "espn"),
            "books_tier": sum(1 for c in legs if c.m.tier == "books"),
            "normalized_event": sum(1 for c in legs if c.m.event_ticker in NORMALIZED)}


def result_json(r: pt.Result, stored: dict | None = None) -> dict:
    out = {"tier": r.tier, "scope": r.scope, "sport": r.sport, "candidates": r.candidates,
           "binding": r.binding, "tried": r.tried,
           "price_seconds": {n: round(s, 3) for n, s in r.price_seconds.items()}, "search_seconds": round(r.seconds, 3)}
    if r.ticket:
        k, p = r.ticket, r.ticket.priced
        out["ticket"] = {
            "agent": k.agent, "legs": len(k.legs), "scored_price": str(p.scored_price),
            "payout_multiple": round(1 / float(p.scored_price), 2),
            "taker_fee_order": str(p.taker_fee_order), "taker_fee": str(p.taker_fee),
            "maker_fee_assumed": str(p.maker_fee_assumed), "fair_p": round(p.fair_p, 8),
            "fair_se": None if p.fair_se is None else round(p.fair_se, 8), "naive_p": str(p.naive_p),
            "rel_cost_over_fair": round(p.rel_cost, 6), "pairs": p.pairs, "measured_pairs": p.measured_pairs,
            "prior_pairs": p.prior_pairs, "combo_status": p.combo_status, "combo_reason": p.combo_reason,
            "edge_legs": k.edge_legs, "fill_note": k.fill_note, "expected_quote": pt.EXPECTED_QUOTE_PENDING,
            "rho_version": k.rho_version, "rho_sha256": k.rho_sha256,
            "sports": sorted({c.m.sport for c in k.legs}), "games": len({c.m.game_ticker for c in k.legs}),
            "leg_reasons": k.leg_reasons,
            "tickers": [f"{c.m.market_ticker}:{c.leg.side}" for c in k.legs],
            "legs_detail": legs_json(k, stored or {}),
        }
    return out


def medians(results: list[pt.Result]) -> dict[str, dict]:
    """Per tier (the cross-sport Lotto apart): tickets drawn and the median relative cost over fair."""
    groups: dict[str, list[float]] = {}
    for r in results:
        key = "lotto cross_sport" if r.scope == "cross_sport" else r.tier
        groups.setdefault(key, [])
        if r.ticket:
            groups[key].append(r.ticket.priced.rel_cost)
    tried: dict[str, list[pt.Result]] = {}
    for r in results:
        tried.setdefault("lotto cross_sport" if r.scope == "cross_sport" else r.tier, []).append(r)
    return {k: {"tickets": len(v), "median_rel_cost": None if not v else round(statistics.median(v), 6),
                "positive": sum(1 for x in v if x > 0),
                # How often the tier filled its window: tickets over the scopes tried, and why the rest were empty.
                "filled": f"{len(v)} of {len(tried[k])}",
                "empty_by_binding": dict(sorted(Counter(r.binding for r in tried[k] if r.ticket is None).items()))}
            for k, v in groups.items()}


def line(r: pt.Result) -> str:
    head = f"{r.tier:5} {('CROSS-SPORT' if r.scope == 'cross_sport' else r.sport or '')[:14]:14} n={r.candidates:<5}"
    if r.ticket is None:
        return f"{head} EMPTY ({r.binding}); search {r.seconds:.2f}s"
    p = r.ticket.priced
    priced = sum(r.price_seconds.values())
    return (f"{head} {len(r.ticket.legs):2} legs  scored {float(p.scored_price):.4f}  pays x{1 / float(p.scored_price):.1f}  "
            f"fee {p.taker_fee}/ct  "
            f"fair {p.fair_p:.4f}  naive {float(p.naive_p):.4f}  rel {p.rel_cost:+.4f}  "
            f"pairs {p.measured_pairs}m/{p.prior_pairs}p of {p.pairs}  {p.combo_status}"
            f"{'  [' + r.ticket.fill_note + ']' if r.ticket.fill_note else ''}; "
            f"pricing {priced:.2f}s over {len(r.price_seconds)} counts, search {r.seconds:.2f}s")


OWNER_TZ = ZoneInfo("America/Chicago")


def chicago_today(when: str) -> dt.datetime:
    """'HH:MM' (today) or 'YYYY-MM-DD HH:MM' in Chicago, as UTC."""
    day, _, hhmm = when.strip().rpartition(" ")
    h, m = (int(x) for x in hhmm.split(":"))
    base = dt.datetime.now(OWNER_TZ)
    if day:
        y, mo, d = (int(x) for x in day.split("-"))
        base = base.replace(year=y, month=mo, day=d)
    local = base.replace(hour=h, minute=m, second=0, microsecond=0)
    return local.astimezone(dt.timezone.utc)


def wait_until(at: dt.datetime) -> None:
    left = (at - dt.datetime.now(dt.timezone.utc)).total_seconds()
    if left > 0:
        print(f"waiting {left:.0f} s until {at:%H:%M:%S}Z", flush=True)
        time.sleep(left)


def ages(quotes: dict, passed: list, at: dt.datetime) -> dict:
    """The oldest re-read quote's age, and the oldest leg passing rules 1-4, at `at` (seconds)."""
    def age(q) -> float:
        return round((at - q.at).total_seconds(), 1)
    out = {"oldest_quote_s": max((age(q) for q in quotes.values()), default=None),
           "oldest_passing_leg_s": max((age(leg.quote) for leg in passed if leg.quote), default=None),
           "limit_s": pl.FRESH.total_seconds()}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env-file", type=Path, required=True)
    ap.add_argument("--draws", type=int, default=cr.MC_DRAWS)
    ap.add_argument("--label", default="slot")
    # A slot rehearsal (owner, Nov 20: Sunday's 10:45 or 16:45 slot, from the owner's machine):
    # wait until --start-at (Chicago, e.g. 10:33) to re-quote, then until --commit-at (e.g. 10:45)
    # to apply rule 2 and measure every leg's age at that moment. Nothing is committed.
    ap.add_argument("--start-at", help="HH:MM America/Chicago: wait, then start the re-quote")
    ap.add_argument("--commit-at", help="HH:MM America/Chicago: the commit moment rule 2 and the ages are measured at")
    args = ap.parse_args(argv)
    global ENV_FILE
    ENV_FILE = args.env_file
    if crawler_running():
        print("A Kalshi research crawler is running on this machine; the dry run waits for it.")
        return 2
    if args.start_at:
        wait_until(chicago_today(args.start_at))
    t0 = time.monotonic()
    with psycopg.connect(readonly(args.env_file)) as conn:
        try:
            conn.execute("SET statement_timeout = 60000")
            rows, cut = ledger.parlay_candidates(conn=conn)
        finally:
            conn.rollback()
    read_s = time.monotonic() - t0
    markets = pl.markets_from_rows(rows)
    rq = pl.requote(pl.requote_order(markets))
    requote_end = dt.datetime.now(dt.timezone.utc)
    if args.commit_at:
        commit = chicago_today(args.commit_at)
        wait_until(commit)
        now = max(commit, dt.datetime.now(dt.timezone.utc))
    else:
        now = requote_end
    stored = {m.market_ticker: m.p_yes for m in markets}
    # The Nov 9 rule (owner, Nov 20): p recomputed from the fresh quotes before anything orders a leg.
    markets = pl.with_fresh_p(markets, rq.quotes)
    NORMALIZED.update(pl.normalized_events(markets, rq.quotes))
    f = pl.funnel(pl.legs_of(markets, rq.quotes), now, cut=cut)
    t_search = time.monotonic()
    s = pt.slot(f.passed, cr.load_table(), draws=args.draws)
    search_s = time.monotonic() - t_search
    report = {
        "label": args.label, "run_at": now.isoformat(timespec="seconds"),
        "git": subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True,
                              text=True).stdout.strip(),
        "dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=Path(__file__).parent,
                                     capture_output=True, text=True).stdout.strip()),
        "draws": args.draws, "candidate_markets": len(markets), "read_seconds": round(read_s, 1),
        "requote": {"events": rq.events_asked, "skipped": rq.events_skipped, "calls": rq.calls,
                    "limited_429": rq.limited, "share_429": round(rq.share_429, 4), "seconds": round(rq.seconds, 1),
                    "bytes": rq.bytes, "pace_end": round(rq.pace, 2)},
        "requote_end": requote_end.isoformat(timespec="seconds"), "measured_at": now.isoformat(timespec="seconds"),
        "ages_at_measure": ages(rq.quotes, f.passed, now),
        "passed_rules_1_4": len(f.passed), "no_consensus_p": s.no_p,
        "search_seconds": round(search_s, 1), "slot_seconds": round(time.monotonic() - t0, 1),
        "results": [result_json(r, stored) for r in s.results],
        "median_by_tier": medians(s.results),
        "leg_mix": leg_mix(s.results),
        "rule_version": pt.RULE_VERSION,
        "stamp": stamp_state(),
    }
    out = DATA_DIR.parent / "parlays"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"tickets_{args.label}_{now:%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    for r in s.results:
        print(line(r))
    print(f"rule version {pt.RULE_VERSION}; stamp: {report['stamp']}; leg mix: {report['leg_mix']}")
    for k, v in report["median_by_tier"].items():
        print(f"median {k}: {v['median_rel_cost']} over {v['tickets']} tickets ({v['positive']} positive); "
              f"filled {v['filled']}; empty by binding {v['empty_by_binding']}")
    print(f"candidates {len(markets)} markets; passed rules 1-4 {len(f.passed)} legs; no consensus p {s.no_p}")
    print(f"re-quote {rq.calls} calls, {rq.limited} 429 ({rq.share_429:.2%}), {rq.seconds:.1f}s, ended "
          f"{requote_end:%H:%M:%S}Z; search {search_s:.1f}s; slot {time.monotonic() - t0:.1f}s")
    print(f"ages at {now:%H:%M:%S}Z: {report['ages_at_measure']}")
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
