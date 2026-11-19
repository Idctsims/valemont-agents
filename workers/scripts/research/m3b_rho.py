"""Step 1, M3b (Chat 8 Phase 3.5): reproduce 20 of nba-hub's NBA rows from public box scores.

    ..\\venv\\Scripts\\python.exe -m scripts.research.m3b_rho fetch --data <repo>\\research_data
    ..\\venv\\Scripts\\python.exe -m scripts.research.m3b_rho reproduce --data <repo>\\research_data --out <repo>\\docs\\research

Owner's machine only. `fetch` makes one public call per season and season
type to stats.nba.com's league game log (2023-24, 2024-25, 2025-26, regular
season and playoffs: the data-rights table, docs/betting_science_audit.md §1)
and stores the raw JSON under `<data>/m3b/` (gitignored). No Kalshi call, no
Supabase read. `reproduce` reads only those files.

THE GATE (§6 M3b, frozen): if fewer than 15 of the 20 rows land within
+-0.05 of nba-hub's value, the NBA table is re-measured from scratch and the
external one retired. Nothing else is measured before this answer.

WHICH 20 ROWS (fixed before any number was computed, 2026-11-19): only rows a
box score can decide. `cover` and `over` need book spreads and totals, which a
box score lacks, so `game|*` and `team|*|cover|over` are out. From the rest:
8 `self`, 4 `mate`, 4 `opp`, 4 `team|*|win`; in each class half are the
largest |rho| and half the lowest crc32 of the key among the others (so
near-zero rows cannot carry the test alone).

HOW (stated, since nba-hub's code is not visible and its lines were book
lines): a player's line for a stat is floor(median of his earlier games this
season) + 0.5, over = above it; a player counts in a game only after 10
earlier games this season with an earlier average of 20+ minutes (a proxy for
having book lines). dd and td are yes/no. Rho is the pooled tetrachoric
(latent-normal) correlation of the 2x2 table over every qualifying pair:
self (one player, two stats), mate (two teammates; a pair once when the stats
match, both orders otherwise), opp (one player per club), team|k|win (a
player's over with his club's win). Interval: 95% bootstrap over games (200
resamples). nba-hub's jackknife shrink and positive-definite projection are
not applied; the n/(n+200) shrink of §6 is reported beside the raw value.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from consensus.correlation import Phi, Phi_inv, bvn_cdf

SEASONS = ("2023-24", "2024-25", "2025-26")
TYPES = ("Regular Season", "Playoffs")
URL = "https://stats.nba.com/stats/leaguegamelog"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://www.nba.com/",
           "Origin": "https://www.nba.com", "Accept": "application/json"}
KINDS = ("pts", "reb", "ast", "tpm", "stl", "blk", "pra", "pr", "pa", "ar", "sb", "dd", "td")
MIN_PRIOR_GAMES, MIN_PRIOR_MINUTES = 10, 20.0
TOLERANCE, GATE = 0.05, 15
BOOT = 200
QUOTA = {"self": 8, "mate": 4, "opp": 4, "team": 4}


# --------------------------------------------------------------------------- selection
def reproducible(key: str) -> bool:
    cls, a, b = key.split("|")
    if cls == "game":
        return False
    if cls == "team":
        return b == "win"
    return a in KINDS and b in KINDS


def pick_rows(rows: dict[str, float]) -> list[str]:
    """The fixed rule in the module docstring."""
    out: list[str] = []
    for cls, n in QUOTA.items():
        keys = [k for k in rows if k.startswith(cls + "|") and reproducible(k)]
        big = sorted(keys, key=lambda k: (-abs(rows[k]), k))[: n // 2]
        rest = sorted((k for k in keys if k not in big), key=lambda k: (zlib.crc32(k.encode()), k))[: n - len(big)]
        out += big + rest
    return out


# --------------------------------------------------------------------------- stats
def stat_values(r: dict[str, Any]) -> dict[str, float]:
    pts, reb, ast, stl, blk = (float(r[k] or 0) for k in ("PTS", "REB", "AST", "STL", "BLK"))
    tens = sum(x >= 10 for x in (pts, reb, ast, stl, blk))
    return {"pts": pts, "reb": reb, "ast": ast, "tpm": float(r["FG3M"] or 0), "stl": stl, "blk": blk,
            "pra": pts + reb + ast, "pr": pts + reb, "pa": pts + ast, "ar": ast + reb, "sb": stl + blk,
            "dd": float(tens >= 2), "td": float(tens >= 3)}


def minutes(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    m, _, s = str(v).partition(":")
    return float(m or 0) + float(s or 0) / 60


def outcomes(rows: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """{game_id: [{player, team, win, over: {kind: 0/1}}]} walk-forward, per season."""
    by_season: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_season[r["SEASON_KEY"]].append(r)
    games: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for season_rows in by_season.values():
        season_rows.sort(key=lambda r: (r["GAME_DATE"], r["GAME_ID"]))
        hist: dict[int, list[tuple[float, dict[str, float]]]] = defaultdict(list)
        for r in season_rows:
            mins = minutes(r["MIN"])
            if mins <= 0:
                continue
            vals = stat_values(r)
            prior = hist[r["PLAYER_ID"]]
            if len(prior) >= MIN_PRIOR_GAMES and statistics.fmean(m for m, _ in prior) >= MIN_PRIOR_MINUTES:
                over = {}
                for k in KINDS:
                    if k in ("dd", "td"):
                        over[k] = int(vals[k])
                    else:
                        line = math.floor(statistics.median(v[k] for _, v in prior)) + 0.5
                        over[k] = int(vals[k] > line)
                games[r["GAME_ID"]].append({"player": r["PLAYER_ID"], "team": r["TEAM_ID"], "win": int(r["WL"] == "W"),
                                            "over": over})
            prior.append((mins, vals))
    return games


def table_for(key: str, game: list[dict[str, Any]]) -> list[int]:
    """The game's 2x2 counts [n00, n01, n10, n11] for one row."""
    cls, a, b = key.split("|")
    t = [0, 0, 0, 0]

    def add(x: int, y: int) -> None:
        t[2 * x + y] += 1

    if cls == "self":
        for p in game:
            add(p["over"][a], p["over"][b])
    elif cls == "team":
        for p in game:
            add(p["over"][a], p["win"])
    else:
        for i, p in enumerate(game):
            for j, q in enumerate(game):
                if i == j or (p["team"] == q["team"]) != (cls == "mate"):
                    continue
                if a == b and j < i:
                    continue
                add(p["over"][a], q["over"][b])
    return t


def tetrachoric(t: Sequence[int]) -> float | None:
    n00, n01, n10, n11 = t
    n = n00 + n01 + n10 + n11
    if n == 0:
        return None
    pa, pb, p11 = (n10 + n11) / n, (n01 + n11) / n, n11 / n
    if not (0 < pa < 1 and 0 < pb < 1):
        return None
    h, k = Phi_inv(1 - pa), Phi_inv(1 - pb)

    def both(r: float) -> float:  # P(Z1 > h, Z2 > k)
        return 1 - Phi(h) - Phi(k) + bvn_cdf(h, k, r)

    lo, hi = -0.9999, 0.9999
    if p11 <= both(lo):
        return lo
    if p11 >= both(hi):
        return hi
    for _ in range(80):
        mid = (lo + hi) / 2
        if both(mid) < p11:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def measure(key: str, games: dict[str, list[dict[str, Any]]], seed: int = 20261119) -> dict[str, Any]:
    per_game = [table_for(key, g) for g in games.values()]
    per_game = [t for t in per_game if sum(t)]
    total = [sum(col) for col in zip(*per_game)]
    rho = tetrachoric(total)
    rng = random.Random(seed ^ zlib.crc32(key.encode()))
    boots = []
    for _ in range(BOOT):
        s = [0, 0, 0, 0]
        for _ in range(len(per_game)):
            t = per_game[rng.randrange(len(per_game))]
            for i in range(4):
                s[i] += t[i]
        r = tetrachoric(s)
        if r is not None:
            boots.append(r)
    boots.sort()
    ci = (boots[int(0.025 * len(boots))], boots[min(len(boots) - 1, int(0.975 * len(boots)))]) if boots else (None, None)
    n_games = len(per_game)
    return {"rho": None if rho is None else round(rho, 4), "ci95": [None if c is None else round(c, 4) for c in ci],
            "games": n_games, "pairs": sum(total), "table": total,
            "rho_shrunk_n_over_n_plus_200": None if rho is None else round(rho * n_games / (n_games + 200), 4)}


# --------------------------------------------------------------------------- io
def fetch(data: Path) -> None:
    out = data / "m3b"
    out.mkdir(parents=True, exist_ok=True)
    for season in SEASONS:
        for st in TYPES:
            path = out / f"leaguegamelog_{season}_{st.replace(' ', '_')}.json"
            if path.exists():
                print(f"have {path.name}")
                continue
            q = {"Counter": 0, "Direction": "ASC", "LeagueID": "00", "PlayerOrTeam": "P", "Season": season,
                 "SeasonType": st, "Sorter": "DATE"}
            req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(q)}", headers=HEADERS)
            body = urllib.request.urlopen(req, timeout=120).read()
            json.loads(body)  # refuse a non-JSON answer before writing it
            path.write_bytes(body)
            print(f"wrote {path.name} ({len(body)} bytes)")
            time.sleep(3)


def load(data: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, info = [], {}
    for path in sorted((data / "m3b").glob("leaguegamelog_*.json")):
        rs = json.loads(path.read_bytes())["resultSets"][0]
        h = rs["headers"]
        season = path.stem.split("_")[1]
        got = [dict(zip(h, r)) | {"SEASON_KEY": season} for r in rs["rowSet"]]
        info[path.name] = {"rows": len(got), "games": len({r["GAME_ID"] for r in got}),
                           "dates": [min(r["GAME_DATE"] for r in got), max(r["GAME_DATE"] for r in got)] if got else None}
        rows += got
    return rows, info


def git_hash() -> str:
    here = Path(__file__).resolve().parent
    h = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", str(Path(__file__).resolve())], cwd=here,
                           capture_output=True, text=True).stdout.strip()
    return h + ("-dirty" if dirty else "")


def reproduce(data: Path, out: Path, table_path: Path) -> int:
    hub = {k: v["rho"] for k, v in json.loads(table_path.read_text(encoding="utf-8"))["rows"]["nba"].items()
           if v.get("src") in ("nba_hub", "nba_hub_unreproduced")}  # the 2026-11-11.4 set, so the 20 stay the same
    keys = pick_rows(hub)
    rows, info = load(data)
    games = outcomes(rows)
    results = []
    for k in keys:
        m = measure(k, games)
        diff = None if m["rho"] is None else round(m["rho"] - hub[k], 4)
        results.append({"key": k, "nba_hub": hub[k], **m, "diff": diff,
                        "within": diff is not None and abs(diff) <= TOLERANCE})
    hits = sum(r["within"] for r in results)
    result = {"study": "M3b reproduction", "script": "workers/scripts/research/m3b_rho.py", "git": git_hash(),
              "run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "rule": {"tolerance": TOLERANCE, "gate": GATE, "quota": QUOTA, "min_prior_games": MIN_PRIOR_GAMES,
                       "min_prior_minutes": MIN_PRIOR_MINUTES, "bootstrap": BOOT},
              "input": {"files": info, "player_rows": len(rows), "games_with_a_qualifying_player": len(games),
                        "qualifying_player_games": sum(len(g) for g in games.values())},
              "within": hits, "of": len(results),
              "verdict": "reproduced (>= 15 of 20)" if hits >= GATE else "NOT reproduced (< 15 of 20): stop",
              "rows": results}
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"m3b_nba_repro_{result['run_at'][:10]}.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"written {path}: {hits} of {len(results)} within +-{TOLERANCE}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("fetch", "reproduce"))
    ap.add_argument("--data", type=Path, required=True, help="the research_data folder")
    ap.add_argument("--out", type=Path, help="output folder (docs/research)")
    ap.add_argument("--table", type=Path, default=Path(__file__).resolve().parents[3] / "docs" / "correlation_rho.json")
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        fetch(args.data)
        return 0
    if args.out is None:
        ap.error("reproduce needs --out")
    return reproduce(args.data, args.out, args.table)


if __name__ == "__main__":
    sys.exit(main())
