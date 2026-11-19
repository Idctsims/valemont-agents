"""Step 1, M4 (Chat 8 Phase 3.5): de-vig robustness on `book_lines`, and the
four-method claim rule on the board as it stands.

    ..\\venv\\Scripts\\python.exe -m scripts.research.m4_devig --env-file <repo>\\.env --out <repo>\\docs\\research

Read-only. Connects as `valemont_readonly` (the same guard as
`scripts/db_inspect.py`), reads narrow columns, excludes test rows, rolls
back. No Kalshi call, no Odds API credit. Pinnacle (J6) is not approved: only
the credit arithmetic is reported.

What it measures (docs/betting_science_audit.md §6 M4, rule frozen there):

1. Per `book_lines` row, the four methods' p for side a (power,
   multiplicative, Shin, additive; `consensus/devig.py`), and their spread in
   cents, by favourite band (the power p of the favourite) and by book.
2. Per consensus line (Odds API rows, the same event, market, subject and
   line, at least two books), the four methods' medians and their span: what
   `p_books_lo`/`p_books_hi` hold on the worker since db/078.
3. The claim rule, "positive under all four methods and at or above the
   margin (2c) under the least favourable", applied to every books-tier leg
   the reader `leg_consensus_for` prices now. Past claims cannot be re-tested
   leg by leg: no Kalshi ask is stored with a consensus row (`inputs` holds
   the books only) and the board is pruned.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import unquote, urlsplit

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

from consensus.devig import METHODS, DevigError, american_to_decimal

ROLE = "valemont_readonly"
MARGIN = 0.02
#: The consensus blend (owner, Nov 9): p = 0.7 x outside + 0.3 x Kalshi mid.
W_OUTSIDE = 0.7
BANDS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001))

BOOK_LINES_SQL = """
SELECT sports_event_id, starts_at, fetched_at, source, league, market, subject, book, line, price_a, price_b
  FROM book_lines
 WHERE NOT is_test
"""

#: Books-tier legs priced now, with the stored span. The games come from the
#: board's events that hold a books row.
CLAIMS_SQL = """
WITH g AS (
  SELECT array_agg(DISTINCT e.game_ticker) AS games
    FROM leg_consensus c
    JOIN kalshi_board b USING (market_ticker)
    JOIN kalshi_board_events e ON e.event_ticker = b.event_ticker
   WHERE c.tier = 'books' AND NOT b.is_test)
SELECT f.market_ticker, f.league, f.market_kind, f.tier, f.n_books, f.p_books, f.p_kalshi, f.p_consensus,
       f.yes_ask, f.no_ask, f.fee_yes, f.fee_no, f.edge_yes, f.edge_no, f.edge_claimed,
       f.stale_kalshi, f.stale_outside, f.starts_at, c.p_books_lo, c.p_books_hi
  FROM g, leg_consensus_for(g.games) f
  JOIN leg_consensus c USING (market_ticker)
 WHERE f.tier = 'books' AND NOT f.is_test
"""

USAGE_SQL = "SELECT * FROM odds_api_usage()"


# --------------------------------------------------------------------------- pure
def four(price_a: int, price_b: int) -> dict[str, float] | None:
    """{method: p of side a}; None when the prices cannot be de-vigged."""
    try:
        odds = [american_to_decimal(price_a), american_to_decimal(price_b)]
        return {name: fn(odds)[0] for name, fn in METHODS}
    except DevigError:
        return None


def overround(price_a: int, price_b: int) -> float:
    return 1 / american_to_decimal(price_a) + 1 / american_to_decimal(price_b) - 1


def band_of(p_fav: float) -> str:
    for lo, hi in BANDS:
        if lo <= p_fav < hi:
            return f"{lo:.1f}-{min(hi, 1.0):.1f}"
    return "?"


def summary(xs: Sequence[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(s), "median": round(statistics.median(s), 3), "p90": round(s[min(len(s) - 1, int(0.9 * len(s)))], 3),
            "max": round(s[-1], 3)}


def family(market: str) -> str:
    return market if market in ("ml", "spread", "total") else "prop"


def per_row(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    out, bad = [], 0
    for r in rows:
        ps = four(r["price_a"], r["price_b"])
        if ps is None:
            bad += 1
            continue
        p_fav = max(ps["power"], 1 - ps["power"])
        out.append(r | {"ps": ps, "p_fav": p_fav, "band": band_of(p_fav),
                        "spread_c": (max(ps.values()) - min(ps.values())) * 100,
                        "overround": overround(r["price_a"], r["price_b"])})
    return out, bad


def consensus_lines(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Odds API rows grouped by event, market, subject and line, two books or more:
    each method's median across books, and the span of those medians."""
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["source"] == "o":
            groups[(r["sports_event_id"], r["market"], r["subject"], r["line"])].append(r)
    out = []
    for key, g in groups.items():
        books = {r["book"]: r for r in g}
        if len(books) < 2:
            continue
        med = {m: statistics.median(r["ps"][m] for r in books.values()) for m, _ in METHODS}
        p_fav = max(med["power"], 1 - med["power"])
        out.append({"key": key, "market": key[1], "league": g[0]["league"], "n_books": len(books), "med": med,
                    "p_fav": p_fav, "band": band_of(p_fav), "span_c": (max(med.values()) - min(med.values())) * 100})
    return out


def claim_test(rows: Sequence[dict[str, Any]], margin: float = MARGIN) -> dict[str, Any]:
    """The frozen rule on each side of each books-tier leg. The least favourable
    method for YES is the lowest median (p_books_lo), for NO the highest; the
    consensus moves by W_OUTSIDE times the books' move."""
    c: dict[str, int] = defaultdict(int)
    blend_err: list[float] = []
    detail = []
    for r in rows:
        f = {k: (float(v) if v is not None and k not in ("market_ticker", "league", "market_kind", "tier", "starts_at")
                 and not isinstance(v, bool) else v) for k, v in r.items()}
        if f["p_kalshi"] is not None and f["p_books"] is not None and f["p_consensus"] is not None:
            blend_err.append(abs(f["p_consensus"] - (W_OUTSIDE * f["p_books"] + (1 - W_OUTSIDE) * f["p_kalshi"])))
        fresh = not f["stale_kalshi"] and not f["stale_outside"]
        has_span = f["p_books_lo"] is not None and f["p_books_hi"] is not None
        c["legs"] += 1
        c["legs fresh"] += fresh
        c["legs with a span"] += has_span
        c["shipped edge_claimed"] += bool(f["edge_claimed"])
        for side in ("yes", "no"):
            e = f[f"edge_{side}"]
            if e is None:
                continue
            c["sides priced"] += 1
            if e > 0:
                c["sides power-positive"] += 1
                c["sides power-positive, fresh"] += fresh
            if e >= margin:
                c["sides power >= margin"] += 1
            if not has_span:
                continue
            drop = W_OUTSIDE * ((f["p_books"] - f["p_books_lo"]) if side == "yes" else (f["p_books_hi"] - f["p_books"]))
            e_least = e - drop
            c["sides with a span"] += 1
            if e_least > 0:
                c["sides positive under all four"] += 1
            if e_least >= margin:
                c["sides pass the rule (all four > 0, least >= margin)"] += 1
                c["sides pass the rule, fresh"] += fresh
            if e > 0:
                detail.append({"market": f["market_ticker"], "side": side, "edge_power_c": round(e * 100, 2),
                               "edge_least_c": round(e_least * 100, 2), "fresh": fresh})
    return {"counts": dict(c), "blend_check_max_abs": round(max(blend_err), 6) if blend_err else None,
            "power_positive_sides": sorted(detail, key=lambda d: -d["edge_power_c"])}


# --------------------------------------------------------------------------- io
def readonly_url(env_file: Path) -> str:
    url = dotenv_values(env_file).get("DATABASE_URL_READONLY") if env_file.is_file() else None
    if not url:
        sys.exit(f"DATABASE_URL_READONLY is not set in {env_file}.")
    user = unquote(urlsplit(url).username or "")
    if user != ROLE and not user.startswith(ROLE + "."):
        sys.exit(f"DATABASE_URL_READONLY connects as {user.split('.')[0]!r}, not {ROLE!r}. Refusing.")
    return url


def fetch(url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    with psycopg.connect(url) as conn, conn.cursor(row_factory=dict_row) as cur:
        try:
            cur.execute("SET statement_timeout = 60000")
            cur.execute(BOOK_LINES_SQL)
            lines = cur.fetchall()
            cur.execute(CLAIMS_SQL)
            claims = cur.fetchall()
            cur.execute(USAGE_SQL)
            usage = cur.fetchone() or {}
        finally:
            conn.rollback()
    return lines, claims, usage


def git_hash() -> str:
    here = Path(__file__).resolve().parent
    h = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", str(Path(__file__).resolve())], cwd=here,
                           capture_output=True, text=True).stdout.strip()
    return h + ("-dirty" if dirty else "")


def analyze(lines: list[dict[str, Any]], claims: list[dict[str, Any]], usage: dict[str, Any]) -> dict[str, Any]:
    rows, bad = per_row(lines)
    by_band: dict[str, list[float]] = defaultdict(list)
    by_book: dict[str, list[float]] = defaultdict(list)
    by_book_or: dict[str, list[float]] = defaultdict(list)
    by_family: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_band[r["band"]].append(r["spread_c"])
        book = r["book"] + (" (espn)" if r["source"] == "e" else "")
        by_book[book].append(r["spread_c"])
        by_book_or[book].append(r["overround"] * 100)
        by_family[f"{r['source']}:{family(r['market'])}"].append(r["spread_c"])
    cons = consensus_lines(rows)
    cons_band: dict[str, list[float]] = defaultdict(list)
    cons_drop: dict[str, list[float]] = defaultdict(list)
    for c in cons:
        cons_band[c["band"]].append(c["span_c"])
        cons_drop[family(c["market"])].append(W_OUTSIDE * c["span_c"])
    return {
        "input": {"book_lines_rows": len(lines), "devigged": len(rows), "refused": bad,
                  "fetched_at": [min(r["fetched_at"] for r in lines).isoformat(), max(r["fetched_at"] for r in lines).isoformat()],
                  "starts_at": [min(r["starts_at"] for r in lines).isoformat(), max(r["starts_at"] for r in lines).isoformat()],
                  "by_source": {s: sum(1 for r in lines if r["source"] == s) for s in sorted({r["source"] for r in lines})},
                  "books_tier_legs_priced_now": len(claims)},
        "row_spread_c_by_band": {b: summary(v) for b, v in sorted(by_band.items())},
        "row_spread_c_by_book": {b: summary(v) for b, v in sorted(by_book.items())},
        "row_overround_pct_by_book": {b: summary(v) for b, v in sorted(by_book_or.items())},
        "row_spread_c_by_source_family": {b: summary(v) for b, v in sorted(by_family.items())},
        "consensus_lines": len(cons),
        "consensus_span_c_by_band": {b: summary(v) for b, v in sorted(cons_band.items())},
        "consensus_edge_lost_c_by_family (0.7 x span)": {b: summary(v) for b, v in sorted(cons_drop.items())},
        "consensus_lines_where_least_favourable_costs_ge_margin": sum(1 for c in cons if W_OUTSIDE * c["span_c"] >= MARGIN * 100),
        "claims_now": claim_test(claims),
        "odds_api_usage": {k: (v.isoformat() if isinstance(v, (dt.datetime, dt.date)) else (float(v) if v is not None and not isinstance(v, (int, str)) else v))
                           for k, v in usage.items()},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env-file", type=Path, required=True, help="the repo's .env (DATABASE_URL_READONLY only is read)")
    ap.add_argument("--out", type=Path, required=True, help="output folder (docs/research)")
    args = ap.parse_args(argv)
    lines, claims, usage = fetch(readonly_url(args.env_file))
    result = {"study": "M4", "script": "workers/scripts/research/m4_devig.py", "git": git_hash(),
              "run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "margin": MARGIN} | analyze(lines, claims, usage)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"m4_devig_{result['run_at'][:10]}.json"
    path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    print(f"written {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
