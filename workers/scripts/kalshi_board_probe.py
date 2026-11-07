"""Chat 8 Phase 1, Step 0: the Kalshi board probe. READ ONLY.

    ..\\venv\\Scripts\\python.exe -m scripts.kalshi_board_probe --out <file.json>   (from workers/)

Measures what the daily board of every open Kalshi sports market must handle,
before any schema exists (the owner reviews the numbers first). It writes
nothing anywhere except the JSON summary named by --out. No auth, no key, no
order endpoint: the existing paced client, with its 429 backoff.

How the 36 h window is found (never the raw open-markets list, which is
flooded with combo markets, MASTER_PLAN §7):

1. `GET /series?category=Sports`: every sports series, with its fee_type and
   fee_multiplier (one call).
2. `GET /milestones?category=Sports&minimum_start_date=<now - 6 h>`: every
   sports fixture with its start and the event tickers it names (primary and
   related). The window is the milestones that start in [now, now + 36 h];
   in-play ones (started in the last 6 h) are counted apart.
3. For each series that owns a window event: `GET /events?series_ticker=…
   &status=open&with_nested_markets=true`, every page. Only events the
   window's milestones name are kept; the bytes spent on the others are
   reported as waste.
4. `GET /multivariate_event_collections?status=open`, every page: field
   names, leg limits, and which window events a collection lists.
5. Fees: every series' fee_type from step 1 through `FeeRegime` (which
   refuses a type it does not model); `fees.fetch_schedule` (2 calls each)
   for the series that own a window event.

Prices are not printed; tickers and counts are.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.memory import PeakSampler  # noqa: E402
from venues.kalshi.client import USER_AGENT, KalshiClient, KalshiError, KalshiUnreachable  # noqa: E402
from venues.kalshi.fees import FeeRegime, UnsupportedFeeType, fetch_schedule  # noqa: E402

WINDOW_H = 36
INPLAY_H = 6
PAGE = 200
MAX_PAGES = 60

#: Milestone league/tour label -> sports_leagues.code (as in kalshi_mapping_probe).
LABELS: dict[str, str] = {
    "NFL": "nfl", "NCAAFB": "cfb", "NBA": "nba", "NCAAMB": "mcbb", "NHL": "nhl",
    "MLB": "mlb", "EPL": "epl", "MLS": "mls", "Champions League": "ucl",
    "UEFA Champions League": "ucl", "UFC": "ufc", "ATP": "atp", "WTA": "wta",
}
#: Ticker prefix -> code, for series no milestone label maps (longest first).
PREFIXES: tuple[tuple[str, str], ...] = tuple(sorted({
    "KXNFL": "nfl", "KXNCAAF": "cfb", "KXNBA": "nba", "KXNCAAMB": "mcbb", "KXNHL": "nhl",
    "KXMLB": "mlb", "KXEPL": "epl", "KXMLS": "mls", "KXUCL": "ucl", "KXUFC": "ufc",
    "KXATP": "atp", "KXWTA": "wta", "KXF1": "f1", "KXPGA": "pga",
}.items(), key=lambda kv: -len(kv[0])))
#: A series ticker ending in one of these is a game line (ML/spread/total).
LINE_SUFFIX = re.compile(r"(GAME|MATCH|FIGHT|SPREAD|TOTAL|TOTALS|ML)$")


class Meter:
    """The client's transport, replaced: counts calls and raw bytes by
    endpoint, keeps the largest page per endpoint."""

    def __init__(self, client: KalshiClient) -> None:
        self.calls: Counter[str] = Counter()
        self.status: Counter[int] = Counter()
        self.bytes: Counter[str] = Counter()
        self.max_page: dict[str, int] = defaultdict(int)
        client._transport = self._send  # type: ignore[assignment]

    @staticmethod
    def _kind(url: str) -> str:
        path = url.split("/trade-api/v2", 1)[1].split("?", 1)[0]
        if path.startswith("/series/fee_changes"):
            return "/series/fee_changes"
        if path.startswith("/series/"):
            return "/series/{t}"
        return path

    def _send(self, url: str) -> tuple[int, Any]:
        kind = self._kind(url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as exc:
            self.calls[kind] += 1
            self.status[exc.code] += 1
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KalshiUnreachable(f"{kind}: {exc}") from exc
        self.calls[kind] += 1
        self.status[status] += 1
        self.bytes[kind] += len(raw)
        self.max_page[kind] = max(self.max_page[kind], len(raw))
        return status, json.loads(raw)


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def ts(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


def league_of(series: str, labels: Counter[str]) -> str:
    for label, _ in labels.most_common():
        if label in LABELS:
            return LABELS[label]
    for prefix, code in PREFIXES:
        if series.startswith(prefix):
            return code
    return "unmapped"


def paged(client: KalshiClient, path: str, key: str, **params: Any):
    cursor = None
    for _ in range(MAX_PAGES):
        body = client.get(path, cursor=cursor, **params)
        items = body.get(key) or []
        yield body, items
        cursor = body.get("cursor")
        if not cursor or not items:
            return
    raise KalshiError(f"{path}: still paging after {MAX_PAGES} pages")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-fees", action="store_true", help="skip fetch_schedule (2 calls per series)")
    args = ap.parse_args()

    client = KalshiClient()
    meter = Meter(client)
    t0 = time.monotonic()
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=WINDOW_H)
    out: dict[str, Any] = {"run_at": iso(now), "window_end": iso(end)}

    with PeakSampler() as rss:
        # ---- a) every sports series ------------------------------------
        body = client.get("/series", category="Sports")
        all_series = {s["ticker"]: s for s in body["series"]}
        series_keys = sorted(next(iter(all_series.values())).keys())
        tag_counts = Counter(t for s in all_series.values() for t in (s.get("tags") or ["(none)"]))
        fee_types = Counter((s.get("fee_type"), str(s.get("fee_multiplier"))) for s in all_series.values())
        del body

        # ---- milestones: the window ------------------------------------
        win_events: dict[str, dict[str, Any]] = {}   # event ticker -> {start, type, label, role}
        inplay_events: set[str] = set()
        ms_types: Counter[str] = Counter()
        ms_detail_keys: Counter[str] = Counter()
        ms_total = 0
        ms_after = 0
        for _, items in paged(client, "/milestones", "milestones", category="Sports",
                              minimum_start_date=iso(now - timedelta(hours=INPLAY_H)), limit=PAGE):
            for m in items:
                ms_total += 1
                start = ts(m.get("start_date"))
                if start is None:
                    continue
                if start > end:
                    ms_after += 1
                    continue
                d = m.get("details") or {}
                ms_detail_keys.update(d.keys())
                ms_types[m.get("type") or "?"] += 1
                label = d.get("league") or d.get("tour") or d.get("competition") or ""
                tickers = [(t, "primary") for t in m.get("primary_event_tickers") or []] + \
                          [(t, "related") for t in m.get("related_event_tickers") or []]
                for t, role in tickers:
                    if start < now:
                        inplay_events.add(t)
                        continue
                    win_events.setdefault(t, {"start": iso(start), "type": m.get("type"),
                                              "label": label, "role": role})

        by_series: dict[str, list[str]] = defaultdict(list)
        for t in win_events:
            by_series[t.split("-", 1)[0]].append(t)
        series_labels: dict[str, Counter[str]] = defaultdict(Counter)
        for t, e in win_events.items():
            series_labels[t.split("-", 1)[0]][e["label"]] += 1

        # ---- b) events tree per series ---------------------------------
        per_series: dict[str, dict[str, Any]] = {}
        market_keys: Counter[str] = Counter()
        strike_fields: Counter[str] = Counter()
        strike_types: Counter[str] = Counter()
        market_types: Counter[str] = Counter()
        found: set[str] = set()
        waste_events = 0
        board_markets = 0
        mve_markets = 0
        for s in sorted(by_series):
            want = set(by_series[s])
            row = {"title": (all_series.get(s) or {}).get("title"), "in_sports_list": s in all_series,
                   "tags": (all_series.get(s) or {}).get("tags"),
                   "league": league_of(s, series_labels[s]),
                   "kind": "game_line" if LINE_SUFFIX.search(s) else "prop_or_other",
                   "window_events": 0, "window_markets": 0, "open_events_fetched": 0, "pages": 0}
            try:
                for _, events in paged(client, "/events", "events", series_ticker=s, status="open",
                                       limit=PAGE, with_nested_markets="true"):
                    row["pages"] += 1
                    for e in events:
                        row["open_events_fetched"] += 1
                        if e.get("event_ticker") not in want:
                            waste_events += 1
                            continue
                        found.add(e["event_ticker"])
                        row["window_events"] += 1
                        for mk in e.get("markets") or []:
                            if mk.get("status") not in ("active", "open", None):
                                continue
                            if (mk.get("ticker") or "").startswith("KXMVE"):
                                mve_markets += 1
                            row["window_markets"] += 1
                            board_markets += 1
                            market_keys.update(mk.keys())
                            market_types[mk.get("market_type") or "?"] += 1
                            strike_types[mk.get("strike_type") or "(none)"] += 1
                            for f in ("custom_strike", "floor_strike", "cap_strike", "functional_strike"):
                                if mk.get(f) not in (None, "", {}):
                                    strike_fields[f] += 1
            except KalshiError as exc:
                row["error"] = str(exc)[:200]
            per_series[s] = row

        # ---- c) collections ---------------------------------------------
        coll: dict[str, Any] = {"status": None}
        try:
            colls = []
            coll_keys: set[str] = set()
            ae_keys: set[str] = set()
            for _, items in paged(client, "/multivariate_event_collections", "multivariate_contracts",
                                  status="open", limit=PAGE):
                for c in items:
                    coll_keys.update(c.keys())
                    for a in c.get("associated_events") or []:
                        ae_keys.update(a.keys())
                    assoc = set(c.get("associated_event_tickers") or [])
                    colls.append({
                        "collection_ticker": c.get("collection_ticker"), "series_ticker": c.get("series_ticker"),
                        "size_min": c.get("size_min"), "size_max": c.get("size_max"),
                        "is_single_market_per_event": c.get("is_single_market_per_event"),
                        "is_all_yes": c.get("is_all_yes"), "is_ordered": c.get("is_ordered"),
                        "open_date": c.get("open_date"), "close_date": c.get("close_date"),
                        "associated_events": len(assoc),
                        "window_events_listed": len(assoc & set(win_events)),
                        "window_series_listed": sorted({t.split("-", 1)[0] for t in assoc & set(win_events)}),
                    })
            coll = {"status": 200, "count": len(colls), "fields": sorted(coll_keys),
                    "associated_events_item_fields": sorted(ae_keys), "collections": colls}
        except KalshiError as exc:
            coll = {"status": "error", "error": str(exc)[:200]}

        # ---- d) fees ------------------------------------------------------
        fetched = datetime.now(timezone.utc)
        rejected: dict[str, str] = {}
        for t, s in all_series.items():
            try:
                FeeRegime(series=t, fee_type=s.get("fee_type"), multiplier=Decimal(str(s.get("fee_multiplier"))),
                          effective_from=None, fetched_at=fetched)
            except (UnsupportedFeeType, Exception) as exc:  # noqa: BLE001 - probe records every refusal
                rejected[t] = f"{s.get('fee_type')!r}: {type(exc).__name__}"
        schedules: dict[str, Any] = {}
        if not args.no_fees:
            for s in sorted(by_series):
                try:
                    sch = fetch_schedule(client, s)
                    cur = sch.regimes[-1]
                    schedules[s] = {"fee_type": cur.fee_type, "multiplier": str(cur.multiplier),
                                    "regimes": len(sch.regimes)}
                except UnsupportedFeeType as exc:
                    schedules[s] = {"rejected": str(exc)[:160]}
                except KalshiError as exc:
                    schedules[s] = {"error": str(exc)[:160]}

    seconds = time.monotonic() - t0
    active = {s: r for s, r in per_series.items() if r["window_markets"]}
    by_league_kind: dict[str, Counter[str]] = defaultdict(Counter)
    for r in active.values():
        by_league_kind[r["league"]][r["kind"] + "_markets"] += r["window_markets"]
        by_league_kind[r["league"]][r["kind"] + "_events"] += r["window_events"]
        by_league_kind[r["league"]]["series"] += 1
    out.update({
        "a_series": {"count": len(all_series), "fields": series_keys, "tags": dict(tag_counts.most_common()),
                     "fee_types": {f"{k[0]} x{k[1]}": v for k, v in fee_types.most_common()}},
        "milestones": {"read": ms_total, "after_window": ms_after, "types_in_window": dict(ms_types),
                       "detail_keys": dict(ms_detail_keys), "window_event_tickers": len(win_events),
                       "inplay_event_tickers": len(inplay_events),
                       "window_tickers_found_open": len(found),
                       "window_tickers_not_open": len(set(win_events) - found)},
        "b_board": {"series_with_window_events": len(by_series), "series_with_markets": len(active),
                    "markets": board_markets, "mve_markets_seen": mve_markets,
                    "events_fetched_outside_window": waste_events,
                    "by_league": {k: dict(v) for k, v in sorted(by_league_kind.items())},
                    "market_fields": dict(market_keys), "market_types": dict(market_types),
                    "strike_types": dict(strike_types), "strike_fields_present": dict(strike_fields),
                    "per_series": per_series},
        "c_collections": coll,
        "d_fees": {"feeregime_rejected": rejected, "rejected_count": len(rejected),
                   "fetch_schedule": schedules},
        "e_cost": {"calls": dict(meter.calls), "http_status": dict(meter.status),
                   "bytes": dict(meter.bytes), "max_page_bytes": dict(meter.max_page),
                   "total_bytes": sum(meter.bytes.values()), "seconds": round(seconds, 1),
                   "rss_end_mb": rss.end_mb, "rss_peak_mb": rss.peak_mb},
    })
    Path(args.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")

    print(f"series {len(all_series)}; milestones read {ms_total}; window event tickers {len(win_events)} "
          f"(found open {len(found)}), in-play {len(inplay_events)}")
    print(f"board: {board_markets} markets in {len(active)} series; MVE seen {mve_markets}; "
          f"events fetched outside window {waste_events}")
    for lg, c in sorted(by_league_kind.items()):
        print(f"  {lg:9s} {dict(c)}")
    print(f"collections: {coll.get('status')} count {coll.get('count')}")
    print(f"fees: FeeRegime rejects {len(rejected)} of {len(all_series)}; schedules {len(schedules)}")
    print(f"calls {sum(meter.calls.values())} status {dict(meter.status)}; bytes {sum(meter.bytes.values()):,}; "
          f"{seconds:.0f} s; rss peak {rss.peak_mb} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
