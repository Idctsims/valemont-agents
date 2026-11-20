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
from pathlib import Path

import psycopg

from consensus import correlation as cr
from consensus import parlay_legs as pl
from consensus import parlay_ticket as pt
from core import ledger
from scripts.parlay_dryrun import crawler_running, readonly
from scripts.research.kalshi_history import DATA_DIR


def legs_json(k: pt.Ticket, stored: dict) -> list[dict]:
    """Each leg: its side, ask and fresh side mid, the recomputed side p and the
    board's stored side p it replaced (the stale input the Nov 20 fix removes)."""
    def side(p, s):
        return None if p is None else (p if s == "yes" else 1 - p)

    return [{"ticker": c.m.market_ticker, "side": c.leg.side, "tier": c.m.tier, "ask": str(c.ask),
             "mid": str(pt.side_mid(c)), "p_fresh": str(c.p), "p_stored": str(side(stored.get(c.m.market_ticker), c.leg.side)),
             "stale_outside": c.m.stale_outside} for c in k.legs]


def result_json(r: pt.Result, stored: dict | None = None) -> dict:
    out = {"tier": r.tier, "scope": r.scope, "sport": r.sport, "candidates": r.candidates,
           "binding": r.binding, "tried": r.tried,
           "price_seconds": {n: round(s, 3) for n, s in r.price_seconds.items()}, "search_seconds": round(r.seconds, 3)}
    if r.ticket:
        k, p = r.ticket, r.ticket.priced
        out["ticket"] = {
            "agent": k.agent, "legs": len(k.legs), "scored_price": str(p.scored_price),
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
    return {k: {"tickets": len(v), "median_rel_cost": None if not v else round(statistics.median(v), 6),
                "positive": sum(1 for x in v if x > 0)} for k, v in groups.items()}


def line(r: pt.Result) -> str:
    head = f"{r.tier:5} {('CROSS-SPORT' if r.scope == 'cross_sport' else r.sport or '')[:14]:14} n={r.candidates:<5}"
    if r.ticket is None:
        return f"{head} EMPTY ({r.binding}); search {r.seconds:.2f}s"
    p = r.ticket.priced
    priced = sum(r.price_seconds.values())
    return (f"{head} {len(r.ticket.legs):2} legs  scored {float(p.scored_price):.4f}  fee {p.taker_fee}/ct  "
            f"fair {p.fair_p:.4f}  naive {float(p.naive_p):.4f}  rel {p.rel_cost:+.4f}  "
            f"pairs {p.measured_pairs}m/{p.prior_pairs}p of {p.pairs}  {p.combo_status}"
            f"{'  [' + r.ticket.fill_note + ']' if r.ticket.fill_note else ''}; "
            f"pricing {priced:.2f}s over {len(r.price_seconds)} counts, search {r.seconds:.2f}s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env-file", type=Path, required=True)
    ap.add_argument("--draws", type=int, default=cr.MC_DRAWS)
    ap.add_argument("--label", default="slot")
    args = ap.parse_args(argv)
    if crawler_running():
        print("A Kalshi research crawler is running on this machine; the dry run waits for it.")
        return 2
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
    now = dt.datetime.now(dt.timezone.utc)
    stored = {m.market_ticker: m.p_yes for m in markets}
    # The Nov 9 rule (owner, Nov 20): p recomputed from the fresh quotes before anything orders a leg.
    markets = pl.with_fresh_p(markets, rq.quotes)
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
        "passed_rules_1_4": len(f.passed), "no_consensus_p": s.no_p,
        "search_seconds": round(search_s, 1), "slot_seconds": round(time.monotonic() - t0, 1),
        "results": [result_json(r, stored) for r in s.results],
        "median_by_tier": medians(s.results),
    }
    out = DATA_DIR.parent / "parlays"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"tickets_{args.label}_{now:%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    for r in s.results:
        print(line(r))
    for k, v in report["median_by_tier"].items():
        print(f"median {k}: {v['median_rel_cost']} over {v['tickets']} tickets ({v['positive']} positive)")
    print(f"candidates {len(markets)} markets; passed rules 1-4 {len(f.passed)} legs; no consensus p {s.no_p}")
    print(f"re-quote {rq.calls} calls, {rq.limited} 429 ({rq.share_429:.2%}), {rq.seconds:.1f}s; "
          f"search {search_s:.1f}s; slot {time.monotonic() - t0:.1f}s")
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
