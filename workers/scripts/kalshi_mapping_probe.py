"""Chat 7 Phase 2, Step 0: the Kalshi ↔ ESPN mapping probe. READ ONLY.

    ..\\venv\\Scripts\\python.exe -m scripts.kalshi_mapping_probe --out <file.json>   (from workers/)

Measures what Phase 2's mapping must handle (docs/kalshi_mapping_probe.md).
It writes nothing anywhere except the JSON summary named by --out.

* **Kalshi**: the existing unauthenticated, paced client. It walks
  series → events (with nested markets and milestones). It never uses the
  keyword search or the raw open-markets list, which is flooded with combo
  bundles (MASTER_PLAN §7).
* **Game series are discovered, not hard-coded.**
  1. Candidates are every sports series whose ticker ends GAME, MATCH or
     FIGHT and whose tags name one of our sports.
  2. Each candidate gets one page of open events with milestones. A
     candidate with no open event gets its newest settled event instead.
  3. A league is identified by the milestone's own label (`details.league`,
     or `details.tour` for tennis), mapped to our codes below.
  4. The moneyline series is the series of the milestone's
     `main_game_event_ticker`.
* **F1 and golf** have no game series. Their winner-level analogue is the
  series a motorsport or golf milestone names as its main event.
* **Supabase**: `sports_events` is read only through scripts/db_inspect.py
  (the read-only role), in one query with narrow columns: league, espn_id,
  starts_at, home, away, status, plus each side's ESPN abbreviation from
  `participants`. It covers only the leagues and dates measured. The bytes
  printed are reported.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from venues.kalshi.client import KalshiClient, KalshiError  # noqa: E402

ET = ZoneInfo("America/New_York")
WORKERS = Path(__file__).resolve().parent.parent

#: Our league code -> how Kalshi's milestones label it. Labels, not tickers.
LABELS: dict[str, tuple[str, ...]] = {
    "nfl": ("NFL",), "cfb": ("NCAAFB",), "nba": ("NBA",), "mcbb": ("NCAAMB",),
    "nhl": ("NHL",), "mlb": ("MLB",), "epl": ("EPL",), "mls": ("MLS",),
    "ucl": ("Champions League", "UEFA Champions League"), "ufc": ("UFC",),
    "atp": ("ATP",), "wta": ("WTA",),
}
#: Tags that make a series a candidate, per sport.
TEAM_TAGS = {"Football", "Basketball", "Hockey", "Baseball", "Soccer", "Tennis", "MMA"}
WINNER_TAGS = {"Motorsport", "Golf"}
#: Leagues measured in full (window, codes, hazards) against sports_events.
MEASURED = ("nfl", "nba", "cfb", "nhl", "mcbb")
WINDOW_DAYS = 21
TICKER_RE = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<time>\d{4})?(?P<rest>[A-Z0-9]+)$")
MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


class Meter:
    """Wraps the client's transport: counts calls by HTTP status."""

    def __init__(self, client: KalshiClient) -> None:
        self.calls: Counter[int] = Counter()
        self._orig = client._transport
        client._transport = self._send  # type: ignore[assignment]

    def _send(self, url: str) -> tuple[int, Any]:
        status, body = self._orig(url)
        self.calls[status] += 1
        return status, body


def ts(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


def label(ms: dict[str, Any]) -> str | None:
    d = ms.get("details") or {}
    return d.get("league") or d.get("tour")


def our_league(lbl: str | None) -> str | None:
    if not lbl:
        return None
    for code, names in LABELS.items():
        if lbl in names:
            return code
    return None


def events(client: KalshiClient, series: str, status: str, *, nested: bool, limit: int = 200,
           pages: int | None = None) -> tuple[list[dict], list[dict]]:
    """Every event (and milestone) of a series in one status, all pages."""
    evs: list[dict] = []
    mss: list[dict] = []
    cursor = None
    n = 0
    while True:
        body = client.get("/events", series_ticker=series, status=status, limit=limit, cursor=cursor,
                          with_milestones="true", with_nested_markets="true" if nested else None)
        evs += body.get("events") or []
        mss += body.get("milestones") or []
        cursor = body.get("cursor")
        n += 1
        if not cursor or not body.get("events") or (pages and n >= pages):
            return evs, mss


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover(client: KalshiClient) -> dict[str, Any]:
    series = client.get("/series", category="Sports").get("series") or []
    by_ticker = {s["ticker"]: s for s in series}
    team = [s for s in series if re.fullmatch(r"KX\w*(GAME|MATCH|FIGHT)", s["ticker"])
            and TEAM_TAGS & set(s.get("tags") or [])]
    winner = [s for s in series if WINNER_TAGS & set(s.get("tags") or [])]
    main_series: dict[str, Counter] = defaultdict(Counter)  # our league -> main-game series seen
    newest: dict[str, str] = {}  # series -> newest milestone start seen (tie-break)
    seen_labels: Counter = Counter()
    open_by_series: dict[str, int] = {}
    for s in sorted(team, key=lambda s: s["ticker"]):
        evs, mss = events(client, s["ticker"], "open", nested=False, pages=1)
        source = "open"
        if not evs:
            body = client.get("/events", series_ticker=s["ticker"], status="settled", limit=1,
                              with_milestones="true")
            evs, mss, source = body.get("events") or [], body.get("milestones") or [], "settled"
        open_by_series[s["ticker"]] = len(evs) if source == "open" else 0
        for m in mss:
            lbl = label(m)
            seen_labels[lbl] += 1
            code = our_league(lbl)
            main = (m.get("details") or {}).get("main_game_event_ticker") or ""
            if code and main:
                s_main = main.split("-")[0]
                main_series[code][(s_main, source)] += 1
                newest[s_main] = max(newest.get(s_main, ""), m.get("start_date") or "")
    winners: dict[str, Counter] = defaultdict(Counter)
    for s in sorted(winner, key=lambda s: s["ticker"]):
        evs, mss = events(client, s["ticker"], "open", nested=False, pages=1)
        for m in mss:
            main = (m.get("details") or {}).get("main_game_event_ticker") or ""
            winners[m.get("type") or "?"][main.split("-")[0] if main else None] += 1
    return {
        "series_total": len(series), "team_candidates": len(team), "winner_candidates": len(winner),
        "candidates_with_open_events": sum(1 for v in open_by_series.values() if v),
        "main_series": {k: {f"{t} ({src})": n for (t, src), n in v.items()} for k, v in main_series.items()},
        # Per league: the most-seen main series, open before settled, newest start breaking a tie.
        "chosen": {k: max(v, key=lambda ts_: (ts_[1] == "open", v[ts_], newest.get(ts_[0], "")))[0]
                   for k, v in main_series.items()},
        "newest_start": newest,
        "winner_main_series": {k: {str(t): n for t, n in v.items()} for k, v in winners.items()},
        "titles": {t: by_ticker[t]["title"] for v in main_series.values() for (t, _) in v
                   if t in by_ticker},
        "labels_seen": dict(seen_labels.most_common()),
    }


# ---------------------------------------------------------------------------
# ESPN side (sports_events via db_inspect)
# ---------------------------------------------------------------------------

def espn_rows(now: datetime) -> tuple[list[dict], int]:
    leagues = ",".join(f"'{x}'" for x in MEASURED)
    sql = (
        "SELECT league, espn_id, starts_at, home, away, status, "
        "participants->'home'->>'abbr' AS home_abbr, participants->'away'->>'abbr' AS away_abbr "
        f"FROM sports_events WHERE NOT is_test AND kind = 'game' AND league IN ({leagues}) "
        f"AND starts_at >= '{(now - timedelta(hours=12)).isoformat()}' "
        f"AND starts_at < '{(now + timedelta(days=WINDOW_DAYS)).isoformat()}' ORDER BY starts_at"
    )
    out = subprocess.run([sys.executable, str(WORKERS / "scripts" / "db_inspect.py"), sql],
                         capture_output=True, cwd=WORKERS, check=True)
    text = out.stdout.decode("utf-8")
    lines = text.splitlines()
    head = lines[0].split("\t")
    rows = [dict(zip(head, ln.split("\t"))) for ln in lines[1:] if ln and not ln.startswith("(")]
    for r in rows:
        r["starts_at"] = datetime.fromisoformat(r["starts_at"])
    return rows, len(out.stdout)


# ---------------------------------------------------------------------------
# Kalshi games
# ---------------------------------------------------------------------------

def kalshi_games(client: KalshiClient, series: str) -> tuple[list[dict], dict[str, int]]:
    """Open and unopened events of one game series, each reduced to: ticker,
    start, title, milestone title, and per side {code, name, team_id}."""
    out: list[dict] = []
    counts: dict[str, int] = {}
    for status in ("open", "unopened"):
        try:
            evs, mss = events(client, series, status, nested=True)
        except KalshiError as exc:  # an unsupported status is a finding, not a crash
            counts[status] = -1
            print(f"  {series} status={status}: {exc}")
            continue
        counts[status] = len(evs)
        by_main = {(m.get("details") or {}).get("main_game_event_ticker"): m for m in mss}
        for e in evs:
            m = by_main.get(e["event_ticker"])
            d = (m or {}).get("details") or {}
            sides = []
            for mk in e.get("markets") or []:
                team_id = next(iter((mk.get("custom_strike") or {}).values()), None)
                sides.append({"code": mk["ticker"].rsplit("-", 1)[-1], "name": mk.get("yes_sub_title"),
                              "team_id": team_id})
            home = next((s for s in sides if s["team_id"] and s["team_id"] == d.get("home_team_id")), None)
            away = next((s for s in sides if s["team_id"] and s["team_id"] == d.get("away_team_id")), None)
            opens = [ts(mk.get("open_time")) for mk in e.get("markets") or [] if mk.get("open_time")]
            out.append({
                "ticker": e["event_ticker"], "status": status, "title": e.get("title"),
                "milestone_title": (m or {}).get("title"), "start": ts((m or {}).get("start_date")),
                "home": home, "away": away, "sides": sides,
                "open_time": min(opens) if opens else None,
            })
    return out, counts


def norm(s: str | None) -> str:
    s = (s or "").lower().replace("&", "and").replace("é", "e")
    s = re.sub(r"\bst\.?(?=\s|$)", "state", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(s.split())


def name_ok(kalshi_name: str | None, espn_name: str) -> bool:
    k, e = norm(kalshi_name), norm(espn_name)
    return bool(k) and (e.startswith(k) or k.startswith(e))


def match(espn: list[dict], kalshi: list[dict]) -> dict[str, Any]:
    """Pair ESPN games with Kalshi events: same start within 3 h and both
    team names matching (home↔home, away↔away). Then a looser pass: same
    start within 3 h and the codes equal ESPN's abbreviations (either order)."""
    used: set[str] = set()
    pairs: list[tuple[dict, dict, str]] = []
    for g in espn:
        best = None
        for k in kalshi:
            if k["ticker"] in used or not k["start"] or abs(k["start"] - g["starts_at"]) > timedelta(hours=3):
                continue
            if k["home"] and k["away"] and name_ok(k["home"]["name"], g["home"]) and name_ok(k["away"]["name"], g["away"]):
                best = (k, "names")
                break
            codes = {s["code"] for s in k["sides"]}
            if codes == {g["home_abbr"], g["away_abbr"]}:
                best = best or (k, "codes")
        if best:
            used.add(best[0]["ticker"])
            pairs.append((g, best[0], best[1]))
    return {"pairs": pairs, "used": used}


def anatomy(games: list[dict]) -> dict[str, Any]:
    """Event ticker format: date part (vs the start's ET date), time part,
    and team order (rest == away+home or home+away)."""
    stats: Counter = Counter()
    examples: list[str] = []
    date_off: list[str] = []
    for k in games:
        m = TICKER_RE.match(k["ticker"])
        if not m:
            stats["unparsed"] += 1
            continue
        stats["with_time" if m["time"] else "date_only"] += 1
        if k["start"]:
            et = k["start"].astimezone(ET)
            d = m["date"]
            if (2000 + int(d[:2]), MONTHS[d[2:5]], int(d[5:])) == (et.year, et.month, et.day):
                stats["date_is_et_date"] += 1
            else:
                stats["date_not_et_date"] += 1
                if len(date_off) < 5:
                    date_off.append(f"{k['ticker']} | {k['milestone_title']} | start {k['start']:%Y-%m-%d %H:%MZ}")
        if k["home"] and k["away"]:
            rest = m["rest"]
            if rest == k["away"]["code"] + k["home"]["code"]:
                stats["away_then_home"] += 1
            elif rest == k["home"]["code"] + k["away"]["code"]:
                stats["home_then_away"] += 1
            else:
                stats["order_other"] += 1
        else:
            stats["no_home_away"] += 1
        if len(examples) < 3:
            examples.append(f"{k['ticker']} | {k['milestone_title']} | start {k['start']:%Y-%m-%d %H:%MZ}"
                            if k["start"] else k["ticker"])
    return {"stats": dict(stats), "examples": examples, "date_not_et_date": date_off}


def hours(xs: list[float]) -> dict[str, float] | None:
    if not xs:
        return None
    xs = sorted(xs)
    return {"min": round(xs[0], 1), "median": round(xs[len(xs) // 2], 1), "max": round(xs[-1], 1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    t0 = time.monotonic()
    now = datetime.now(timezone.utc)
    client = KalshiClient(min_interval=0.25)
    meter = Meter(client)
    result: dict[str, Any] = {"run_at": now.isoformat()}

    print("discovering series ...")
    disc = discover(client)
    result["discovery"] = disc
    game_series = disc["chosen"]
    result["game_series"] = game_series
    print("game series:", game_series)

    espn, espn_bytes = espn_rows(now)
    result["espn"] = {"rows": len(espn), "bytes_printed": espn_bytes,
                      "by_league": dict(Counter(r["league"] for r in espn)),
                      "max_starts_at": {lg: max((r["starts_at"] for r in espn if r["league"] == lg), default=None)
                                        for lg in MEASURED}}

    per: dict[str, Any] = {}
    for lg in MEASURED:
        s = game_series.get(lg)
        if not s:
            per[lg] = {"series": None}
            continue
        print(f"{lg}: {s} ...")
        ks, counts = kalshi_games(client, s)
        eg = [r for r in espn if r["league"] == lg]
        m = match(eg, ks)
        pairs = m["pairs"]
        matched_espn = {g["espn_id"] for g, _, _ in pairs}
        upcoming = [g for g in eg if g["starts_at"] > now and g["status"] == "scheduled"]
        missing = [g for g in upcoming if g["espn_id"] not in matched_espn]
        k_future = [k for k in ks if k["start"] and k["start"] > now]
        horizon = max((g["starts_at"] for g in eg), default=None)
        k_unmatched = [k for k in ks if k["ticker"] not in m["used"] and k["start"]
                       and now - timedelta(hours=12) <= k["start"] <= (horizon or now)]
        # Team codes: ESPN abbr vs Kalshi code, per matched game, both sides.
        diffs: dict[str, str] = {}
        same: set[str] = set()
        for g, k, _ in pairs:
            for side in ("home", "away"):
                e_abbr = g[f"{side}_abbr"]
                kside = k[side]
                if not kside:
                    continue
                if kside["code"] == e_abbr:
                    same.add(e_abbr)
                else:
                    diffs[f"{g[side]} ({e_abbr})"] = kside["code"]
        # Hazards.
        pair_key = lambda d, a, b: (d, frozenset((a, b)))  # noqa: E731
        e_twice = Counter(pair_key(g["starts_at"].astimezone(ET).date(), g["home"], g["away"]) for g in eg)
        k_twice = Counter(pair_key(k["start"].astimezone(ET).date(), *(s["code"] for s in k["sides"][:2]))
                          for k in ks if k["start"] and len(k["sides"]) >= 2)
        vs_titles = [k["milestone_title"] for k in ks if k["milestone_title"] and " vs " in k["milestone_title"]]
        k_last = max((k["start"] for k in k_future), default=None)
        inside = [g for g in missing if k_last and g["starts_at"] <= k_last]
        # For a game missing inside Kalshi's range: any Kalshi event sharing a
        # team (code = ESPN abbr, or name) at a different time?
        near = {}
        for g in inside:
            hits = [f"{k['ticker']} start {k['start']:%Y-%m-%d %H:%MZ}" for k in ks
                    if k["ticker"] not in m["used"] and any(
                        s["code"] in (g["home_abbr"], g["away_abbr"]) or name_ok(s["name"], g["home"])
                        or name_ok(s["name"], g["away"]) for s in k["sides"])]
            if hits:
                near[g["espn_id"]] = hits[:3]
        compared_codes = {k[side]["code"] for _, k, _ in pairs for side in ("home", "away") if k[side]}
        uncompared = sorted({f"{s['code']} ({s['name']})" for k in ks for s in k["sides"]
                             if s["code"] not in compared_codes and s["code"] != "TIE"})
        lead = [(k["start"] - k["open_time"]).total_seconds() / 3600 for k in ks
                if k["start"] and k["open_time"]]
        no_milestone = [k["ticker"] for k in ks if not k["milestone_title"]]
        per[lg] = {
            "series": s, "kalshi_events": counts,
            "kalshi_start_range": [min((k["start"] for k in ks if k["start"]), default=None),
                                   max((k["start"] for k in ks if k["start"]), default=None)],
            "espn_games": len(eg), "espn_upcoming_scheduled": len(upcoming), "espn_horizon": horizon,
            "matched": len(pairs), "matched_by": dict(Counter(how for _, _, how in pairs)),
            "upcoming_missing_on_kalshi": [
                f"{g['starts_at']:%Y-%m-%d %H:%MZ} {g['away']} at {g['home']} ({g['espn_id']})" for g in missing],
            "missing_inside_kalshi_range": [
                f"{g['starts_at']:%Y-%m-%d %H:%MZ} {g['away']} at {g['home']} ({g['espn_id']})" for g in inside],
            "missing_inside_same_team_elsewhere": near,
            "kalshi_codes_not_compared": uncompared,
            "listing_lead_hours": hours(lead),
            "run_now_to_furthest_hours": round((k_last - now).total_seconds() / 3600, 1) if k_last else None,
            "furthest_espn_with_kalshi": max((g["starts_at"] for g, _, _ in pairs), default=None),
            "nearest_upcoming_without": min((g["starts_at"] for g in missing), default=None),
            "kalshi_furthest_listed": max((k["start"] for k in k_future), default=None),
            "kalshi_unmatched_in_espn_range": [f"{k['ticker']} | {k['milestone_title']}" for k in k_unmatched],
            "anatomy": anatomy(ks),
            "codes_compared": len(same) + len(diffs), "codes_same": len(same), "code_diffs": diffs,
            "same_pair_same_date": {"espn": sum(1 for v in e_twice.values() if v > 1),
                                    "kalshi": sum(1 for v in k_twice.values() if v > 1)},
            "milestone_vs_titles": vs_titles, "events_without_milestone": no_milestone,
            "home_away_unresolved": sum(1 for k in ks if not (k["home"] and k["away"])),
        }

    # MLB doubleheaders: Kalshi's settled 2026 season, tickers only.
    mlb = game_series.get("mlb")
    if mlb:
        print(f"mlb doubleheaders: {mlb} settled ...")
        evs, _ = events(client, mlb, "settled", nested=False)
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        odd: list[str] = []
        timed = 0
        for e in evs:
            mt = TICKER_RE.match(e["event_ticker"])
            if not mt:
                odd.append(e["event_ticker"])
                continue
            timed += bool(mt["time"])
            # Same ET date and same team letters, whatever follows them.
            groups[(mt["date"], re.sub(r"G?\d+$", "", mt["rest"]))].append(e["event_ticker"])
        twins = [v for v in groups.values() if len(v) > 1]
        result["mlb_doubleheaders"] = {
            "settled_events": len(evs), "unparsed": odd[:10], "unparsed_count": len(odd),
            "with_time_part": timed, "same_date_same_teams_groups": len(twins),
            "examples": twins[:4],
            "g_suffix": [e["event_ticker"] for e in evs if re.search(r"G\d$", e["event_ticker"])][:6],
        }

    # Ticker anatomy for the other discovered series (open events, else settled).
    other = {}
    for lg, s in game_series.items():
        if lg in MEASURED:
            continue
        evs, mss = events(client, s, "open", nested=True, pages=1)
        src = "open"
        if not evs:
            body = client.get("/events", series_ticker=s, status="settled", limit=3,
                              with_milestones="true", with_nested_markets="true")
            evs, mss, src = body.get("events") or [], body.get("milestones") or [], "settled"
        by_main = {(m.get("details") or {}).get("main_game_event_ticker"): m for m in mss}
        other[lg] = {"series": s, "source": src, "examples": [
            f"{e['event_ticker']} | {(by_main.get(e['event_ticker']) or {}).get('title')} | "
            f"start {(by_main.get(e['event_ticker']) or {}).get('start_date')} | markets "
            + ",".join(mk["ticker"].rsplit('-', 1)[-1] for mk in e.get("markets") or [])
            for e in evs[:3]]}
    result["other_anatomy"] = other
    result["per_league"] = per
    result["calls"] = {str(k): v for k, v in meter.calls.items()}
    result["calls_total"] = sum(meter.calls.values())
    result["rate_limited_429"] = meter.calls.get(429, 0)
    result["seconds"] = round(time.monotonic() - t0, 1)
    Path(args.out).write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(f"done: {result['calls_total']} calls, 429s {result['rate_limited_429']}, "
          f"{result['seconds']} s; ESPN {espn_bytes} bytes printed; wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
