"""Dry run of the Kalshi mapping (Chat 7 Phase 2): the worker's own matching
(sports/kalshi_map.decide_league) against production rows and Kalshi's live
listing, WRITING NOTHING. For the report before db/066 is pasted, and to
check a link rate any time after.

    ..\\venv\\Scripts\\python.exe -m scripts.kalshi_map_dry_run --out <json>     (from workers/)

  * Database: only as valemont_readonly (scripts/db_inspect.readonly_url),
    narrow columns, every result metered (core/egress.result_bytes). Before
    the paste the link columns do not exist and every row reads as never
    evaluated; after it, the stored state is used, as the job would.
  * Registry and aliases: read from db/066's marked blocks (KALSHI REGISTRY,
    ALIASES), so the run tests exactly the seed under review.
  * Kalshi: public, the read-only client, series -> events -> markets.

Prints per league linked / not_listed / not_linked / out_of_scope with every
not_linked reason (ESPN names added for reading), Kalshi events no ESPN row
claims, and calls, 429s, time and KB read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from core import egress, ledger
from core.paths import REPO_ROOT
from scripts.db_inspect import readonly_url
from sports import kalshi_map as km
from venues.kalshi.client import KalshiClient, KalshiError

MIGRATION = REPO_ROOT / "db" / "066_kalshi_mapping.sql"
_TUPLE = re.compile(r"^\s*\((.*)\),?\s*(--.*)?$")


def _values(sql: str, name: str) -> list[list[str]]:
    """Every VALUES tuple between '-- <name> BEGIN' and '-- <name> END',
    split into its SQL literals (strings with '' escapes, integers)."""
    # The rest of the BEGIN marker's own line is a comment, not a tuple.
    block = sql.split(f"-- {name} BEGIN", 1)[1].split("\n", 1)[1].split(f"-- {name} END", 1)[0]
    out: list[list[str]] = []
    for line in block.splitlines():
        m = _TUPLE.match(line)
        if not m or line.lstrip().startswith("--"):
            continue
        out.append(re.findall(r"'(?:[^']|'')*'|\d+|NULL|true|false", m.group(1)))
    return out


def _lit(s: str) -> str | int:
    return s[1:-1].replace("''", "'") if s.startswith("'") else int(s)


def migration_seed(path: Path = MIGRATION) -> tuple[dict[str, tuple[str, str, int]], list[tuple[str, str, str, str]]]:
    """({code: (series, map, days_ahead)}, [(league, espn, kalshi, note)])
    from db/066's marked blocks. Raises if a tuple has the wrong arity."""
    sql = path.read_text(encoding="utf-8")
    reg: dict[str, tuple[str, str, int]] = {}
    for t in _values(sql, "KALSHI REGISTRY"):
        if len(t) != 4:
            raise ValueError(f"registry tuple with {len(t)} values: {t}")
        code, series, kmap, days = (_lit(x) for x in t)
        reg[str(code)] = (str(series), str(kmap), int(days))
    aliases: list[tuple[str, str, str, str]] = []
    for t in _values(sql, "ALIASES"):
        if len(t) != 4:
            raise ValueError(f"alias tuple with {len(t)} values: {t}")
        league, espn, kalshi, note = (str(_lit(x)) for x in t)
        aliases.append((league, espn, kalshi, note))
    return reg, aliases


ROWS_PRE_066 = """
    SELECT id, league, kind, session, starts_at,
           participants->'home'->>'abbr', participants->'away'->>'abbr',
           CASE WHEN kind IN ('match', 'bout') AND jsonb_typeof(participants) = 'array'
                THEN (SELECT array_agg(p->>'name') FROM jsonb_array_elements(participants) p) END,
           NULL::text, NULL::text, NULL::text
      FROM sports_events
     WHERE is_test = %(is_test)s AND starts_at >= %(since)s AND league = ANY(%(leagues)s)
     ORDER BY league, starts_at, id
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="where to write the JSON result")
    args = ap.parse_args(argv)
    t0 = time.monotonic()
    now = datetime.now(timezone.utc)
    reg, alias_rows = migration_seed()
    aliases = {(lg, e): k for lg, e, k, _ in alias_rows}
    kb = Counter()
    with psycopg.connect(readonly_url()) as conn:
        conn.execute("SET statement_timeout = 30000")
        cur = conn.cursor()
        cur.execute("SELECT code, kind, sport FROM sports_leagues WHERE active ORDER BY sort")
        leagues = [km.MapLeague(code, kind, sport, reg[code][0], reg[code][1]) for code, kind, sport in cur.fetchall()]
        kb["registry"] += egress.result_bytes(cur)
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'sports_events' "
                    "AND column_name = 'link_state'")
        pasted = cur.fetchone() is not None
        cur.execute(ledger.KALSHI_MAP_ROWS_SQL if pasted else ROWS_PRE_066,
                    {"since": now - km.EVAL_BACK, "leagues": [lg.code for lg in leagues], "is_test": False})
        raw = cur.fetchall()
        kb["rows (the job's read)"] += egress.result_bytes(cur)
        rows = [ledger.MapRow(int(i), lg, kind, sess, st, h, a, tuple(n for n in (names or ()) if n), state, tk, cf)
                for i, lg, kind, sess, st, h, a, names, state, tk, cf in raw]
        client = KalshiClient(min_interval=km.MIN_INTERVAL_S)
        meter = km.Meter(client)
        run = km.MapRun()
        bad = km._check_contract(client, leagues, run)
        report: dict[str, dict] = {}
        by = {}
        for r in rows:
            by.setdefault(r.league, []).append(r)
        t_kalshi = time.monotonic()
        for lg in leagues:
            lrows = by.get(lg.code, [])
            if not lrows or lg.series in bad:
                report[lg.code] = {"rows": len(lrows), "skipped": "no rows" if not lrows else "series check failed"}
                continue
            try:
                events = km.fetch_open(client, lg.series or "", run) if lg.method != "off" else []
            except KalshiError as exc:
                report[lg.code] = {"rows": len(lrows), "failed": str(exc)}
                continue
            res = km.decide_league(lg, lrows, events, aliases, now)
            byid = {r.id: r for r in lrows}
            report[lg.code] = {
                "series": lg.series, "method": lg.method, "rows": len(lrows), "kalshi_open_events": len(events),
                "horizon": (h.isoformat() if (h := km.horizon(events, now)) else None),
                "counts": {s: res.counts[s] for s in km.STATES if res.counts[s]},
                "swapped": sorted(d.ticker for d in res.decisions if d.swapped),
                "not_linked": [{"id": d.id, "starts_at": byid[d.id].starts_at.isoformat(),
                                "codes": [byid[d.id].away_abbr, byid[d.id].home_abbr], "names": byid[d.id].names,
                                "reason": d.reason} for d in res.decisions if d.state == km.NOT_LINKED],
                "not_listed_first": min((byid[d.id].starts_at.isoformat() for d in res.decisions
                                         if d.state == km.NOT_LISTED), default=None),
                "linked_last": max((byid[d.id].starts_at.isoformat() for d in res.decisions
                                    if d.state == km.LINKED), default=None),
                "kalshi_unmatched": res.unmatched,
                "conflicts": res.conflicts,
                "aliases_used": sorted({f"{r.home_abbr}" for r in lrows if (lg.code, r.home_abbr) in aliases}
                                       | {f"{r.away_abbr}" for r in lrows if (lg.code, r.away_abbr) in aliases}),
            }
        kalshi_s = time.monotonic() - t_kalshi
        # ESPN names for the rows not linked, for reading only (metered apart).
        ids = [x["id"] for v in report.values() for x in v.get("not_linked", [])]
        if ids:
            cur.execute("SELECT id, away, home, event_name FROM sports_events WHERE id = ANY(%s)", (ids,))
            names = {i: (a, h, n) for i, a, h, n in cur.fetchall()}
            kb["names for the report"] += egress.result_bytes(cur)
            for v in report.values():
                for x in v.get("not_linked", []):
                    a, h, n = names.get(x["id"], (None, None, None))
                    x["espn"] = f"{a} at {h}" if a and h else n
        conn.rollback()
    out = {"run_at": now.isoformat(), "db066_pasted": pasted, "leagues": report,
           "contract": run.contract, "contract_failed": run.failed,
           "kalshi_calls": meter.calls, "rate_limited_429": meter.limited,
           "kalshi_seconds": round(kalshi_s, 1), "seconds": round(time.monotonic() - t0, 1),
           "db_bytes_read": dict(kb), "aliases_in_seed": len(alias_rows)}
    Path(args.out).write_text(json.dumps(out, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    for code, v in report.items():
        print(code, v.get("counts") or v.get("skipped") or v.get("failed"), "| unmatched",
              len(v.get("kalshi_unmatched", [])))
    print("calls", meter.calls, "429", meter.limited, "kalshi s", round(kalshi_s, 1), "db bytes", dict(kb),
          run.contract, run.failed or "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
