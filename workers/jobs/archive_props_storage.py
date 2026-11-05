"""Store-only archive of one NFL week's props data to Supabase Storage.

    python -m jobs.archive_nfl_props --week 6 --to-storage [--revision N]

Design and rules: docs/props_archive_storage.md. In short:

* the same selection as the Postgres job (jobs/archive_nfl_props.py), as four
  deterministic gzip-JSONL objects per week: markets, settlements, candles,
  prints, under ``2026/week-NN/rR/`` in the private bucket;
* a manifest line per object in ``docs/props_archive/manifest.jsonl``
  (committed to git: git history is the tamper record);
* a week with any game before kickoff + 5 h is refused whole;
* a key is written once: HEAD first, never PUT over an existing object;
* rerunning a revision in the manifest: identical sha256s and sizes is a no-op;
  any mismatch refuses (exit 2) and uploads nothing;
* revision N ≥ 2 only when revision N − 1 recorded unsettled markets.

**Store only (preregistration_nfl.md §8.1).** It never reads an object back and
never writes Postgres. It logs only what the Postgres job logs (markets,
settled, unsettled, candle and print rows, games pending), plus byte counts and
hashes.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Iterable, Mapping, Protocol, Sequence

from core.paths import REPO_ROOT
from core.s3 import ObjectInfo
from jobs.archive_nfl_props import (
    CANDLE_FETCH, KEEP_WINDOW, SEASON, SERIES, keep_candles, ready, week_games,
)

log = logging.getLogger("valemont.archive_props_storage")

FIRST_STORAGE_WEEK: Final = 6
LAST_STORAGE_WEEK: Final = 18
KINDS: Final = ("markets", "settlements", "candles", "prints")
CONTENT_TYPE: Final = "application/gzip"
MANIFEST: Final = REPO_ROOT / "docs" / "props_archive" / "manifest.jsonl"
JOB: Final = "jobs.archive_nfl_props --to-storage"
WINDOW: Final = {
    "candles_fetch": "kickoff-24h..kickoff (1-minute)",
    "candles_keep": "latest ending before kickoff-3h, plus all ending in kickoff-3h..kickoff",
    "prints": "kickoff-3h <= created_time < kickoff",
}

EXIT_OK: Final = 0
EXIT_REFUSED: Final = 2


class Refused(RuntimeError):
    """A rule of docs/props_archive_storage.md §6 said no. Nothing was uploaded
    after the refusal, and no manifest line was written."""


class Store(Protocol):
    bucket: str

    def head(self, key: str) -> ObjectInfo | None: ...

    def put(self, key: str, body: bytes, content_type: str) -> str: ...


# ---------------------------------------------------------------------------
# Serialization: deterministic bytes
# ---------------------------------------------------------------------------

def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("naive timestamp in an archive row")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def serialize(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """gzip(JSONL): sorted keys, compact, level 9, mtime 0, no file name. The
    same rows give the same bytes on any machine, any day."""
    text = "".join(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
                   for r in rows)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, compresslevel=9, mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    return buf.getvalue()


@dataclass
class Week:
    week: int
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {k: [] for k in KINDS})
    counts: dict[str, int] = field(default_factory=dict)
    tiers: dict[str, int] = field(default_factory=lambda: {"live": 0, "historical": 0})
    games: int = 0
    pending: list[str] = field(default_factory=list)
    kickoffs: list[datetime] = field(default_factory=list)
    fetched_at: datetime | None = None

    def sorted_rows(self, kind: str) -> list[dict[str, Any]]:
        key = {
            "markets": lambda r: r["ticker"],
            "settlements": lambda r: r["ticker"],
            "candles": lambda r: (r["ticker"], r["end_period_ts"]),
            "prints": lambda r: (r["ticker"], r["created_time"], r["trade_id"] or ""),
        }[kind]
        return sorted(self.rows[kind], key=key)


@dataclass(frozen=True)
class Built:
    kind: str
    key: str
    body: bytes
    rows: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def md5(self) -> str:
        return hashlib.md5(self.body).hexdigest()  # noqa: S324 - ETag comparison, not security


def object_key(week: int, revision: int, kind: str) -> str:
    return f"{SEASON}/week-{week:02d}/r{revision}/{kind}.jsonl.gz"


# ---------------------------------------------------------------------------
# Fetch (the Postgres job's selection, no database)
# ---------------------------------------------------------------------------

def collect(week: int, schedule: Any, client: Any, now: datetime) -> Week:
    games = week_games(schedule, week)
    by_id = {g.game_id: g for g in games}
    out = Week(week=week, games=len(games), fetched_at=now,
               pending=sorted(g.game_id for g in games if not ready(g, now)),
               kickoffs=sorted(g.kickoff for g in games))
    if out.pending:
        return out  # refused whole by the caller; fetch nothing
    cutoff = client.historical_cutoff()
    seen: set[str] = set()
    for series in SERIES:
        quotes = list(client.markets(series_ticker=series))
        if any(g.kickoff < cutoff for g in games):
            quotes += client.historical_markets(series_ticker=series)
        for q in quotes:
            game = schedule.for_event(q.event_ticker)
            if game is None or game.game_id not in by_id or q.ticker in seen:
                continue
            seen.add(q.ticker)
            out.rows["markets"].append({
                "ticker": q.ticker, "event_ticker": q.event_ticker, "series_ticker": series,
                "sport": "nfl", "game_id": game.game_id, "kickoff": _ts(game.kickoff),
                "kickoff_source": "nflverse", "floor_strike": _num(q.floor_strike),
                "title": q.title or None,
            })
            if q.settlement_value is not None and q.settlement_ts is not None:
                out.rows["settlements"].append({
                    "ticker": q.ticker, "result": q.result,
                    "settlement_value": _num(q.settlement_value),
                    "settlement_ts": _ts(q.settlement_ts),
                })
            historical = q.settlement_ts is not None and q.settlement_ts < cutoff
            out.tiers["historical" if historical else "live"] += 1
            k = game.kickoff
            kept = keep_candles(
                client.candles(series, q.ticker, k - CANDLE_FETCH, k, 1, settled_at=q.settlement_ts), k)
            for c in kept:
                out.rows["candles"].append({
                    "ticker": q.ticker, "period_minutes": 1, "end_period_ts": _ts(c.end),
                    "yes_bid_close": _num(c.yes_bid_close), "yes_ask_close": _num(c.yes_ask_close),
                    "trade_close": _num(c.trade_close), "volume": _num(c.volume),
                    "open_interest": _num(c.open_interest),
                })
            for t in client.trades(q.ticker, k - KEEP_WINDOW, k, settled_at=q.settlement_ts):
                out.rows["prints"].append({
                    "ticker": q.ticker, "trade_id": t.trade_id, "created_time": _ts(t.at),
                    "yes_price": _num(t.yes_price), "count": _num(t.count), "taker_side": t.taker_side,
                })
    n = {k: len(v) for k, v in out.rows.items()}
    out.counts = {
        "markets": n["markets"], "settled": n["settlements"],
        "unsettled": n["markets"] - n["settlements"],
        "candles": n["candles"], "prints": n["prints"],
        "prints_without_id": sum(1 for p in out.rows["prints"] if not p["trade_id"]),
        "games": out.games, "games_pending": len(out.pending),
    }
    return out


def build(data: Week, revision: int) -> list[Built]:
    return [Built(kind=k, key=object_key(data.week, revision, k),
                  body=serialize(data.sorted_rows(k)), rows=len(data.rows[k])) for k in KINDS]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if raw.strip():
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise Refused(f"{path.name} line {i} is not JSON ({exc.msg}); fix it by hand") from None
    return lines


def entries(manifest: Sequence[Mapping[str, Any]], week: int, revision: int) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for line in manifest:
        if line.get("season") == SEASON and line.get("week") == week and line.get("revision") == revision:
            if line["kind"] in out:
                raise Refused(f"manifest has two {line['kind']} lines for week {week} r{revision}")
            out[line["kind"]] = line
    return out


def append_manifest(path: Path, lines: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")


def manifest_line(b: Built, data: Week, revision: int, bucket: str, etag: str,
                  head: ObjectInfo, recovered: bool) -> dict[str, Any]:
    line: dict[str, Any] = {
        "season": SEASON, "week": data.week, "revision": revision, "kind": b.kind,
        "bucket": bucket, "path": b.key, "rows": b.rows, "bytes": len(b.body),
        "sha256": b.sha256, "md5": b.md5, "etag": etag, "etag_is_md5": etag == b.md5,
        "head_bytes": head.size,
        "fetched_at_worker": _ts(data.fetched_at) if data.fetched_at else None,
        "window": WINDOW,
        "kickoff_range": [_ts(data.kickoffs[0]), _ts(data.kickoffs[-1])] if data.kickoffs else None,
        "week_counts": data.counts, "tiers": data.tiers, "job": JOB,
    }
    if recovered:
        line["recovered"] = True
    return line


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def check_revision(manifest: Sequence[Mapping[str, Any]], week: int, revision: int) -> None:
    """Rule 4: a new revision only after the previous one recorded unsettled
    markets. Checked before the (half-hour) fetch."""
    if revision < 1:
        raise Refused(f"revision must be 1 or more, not {revision}")
    if revision == 1 or entries(manifest, week, revision):
        return
    prev = entries(manifest, week, revision - 1)
    if not prev:
        raise Refused(f"week {week} r{revision - 1} is not in the manifest; run r{revision - 1} first")
    unsettled = next(iter(prev.values()))["week_counts"].get("unsettled", 0)
    if not unsettled:
        raise Refused(f"week {week} r{revision - 1} recorded 0 unsettled markets; "
                      f"a revision {revision} is only for late settlements")


def run(week: int, revision: int, *, schedule: Any, client: Any, store: Store,
        manifest_path: Path = MANIFEST, now: datetime) -> int:
    try:
        return _run(week, revision, schedule=schedule, client=client, store=store,
                    manifest_path=manifest_path, now=now)
    except Refused as exc:
        log.error("REFUSED week %d r%d: %s", week, revision, exc)
        return EXIT_REFUSED


def _run(week: int, revision: int, *, schedule: Any, client: Any, store: Store,
         manifest_path: Path, now: datetime) -> int:
    if not FIRST_STORAGE_WEEK <= week <= LAST_STORAGE_WEEK:
        raise Refused(f"storage holds weeks {FIRST_STORAGE_WEEK}–{LAST_STORAGE_WEEK}; "
                      f"week {week} stays in Postgres")
    manifest = load_manifest(manifest_path)
    check_revision(manifest, week, revision)
    recorded = entries(manifest, week, revision)

    data = collect(week, schedule, client, now)
    if data.pending:
        raise Refused(f"{len(data.pending)} game(s) not yet past kickoff + 5 h: {data.pending}; "
                      "a week is uploaded whole or not at all")
    if not data.rows["markets"]:
        raise Refused(f"no markets found for week {week}'s {data.games} games")
    built = build(data, revision)
    log.info("week %d r%d fetched: %s", week, revision, data.counts)

    if recorded:  # rule 3: a rerun of a revision already recorded
        if set(recorded) != set(KINDS):
            raise Refused(f"manifest has {sorted(recorded)} for week {week} r{revision}, not all four")
        bad = [b.kind for b in built if recorded[b.kind]["sha256"] != b.sha256]
        if bad:
            raise Refused(f"week {week} r{revision}: rebuilt {bad} differ from the manifest's sha256; "
                          "nothing uploaded. If settlements arrived late, use --revision "
                          f"{revision + 1} (allowed only if r{revision} recorded unsettled markets)")
        for b in built:
            head = store.head(b.key)
            if head is None or head.size != recorded[b.kind]["bytes"]:
                raise Refused(f"{store.bucket}/{b.key} is in the manifest but storage has "
                              f"{'nothing' if head is None else f'{head.size} bytes'}")
        log.info("week %d r%d: all four objects match the manifest and storage — no-op", week, revision)
        return EXIT_OK

    # Rule 2 and 5: HEAD all four before uploading any.
    heads = {b.kind: store.head(b.key) for b in built}
    recovered = set()
    for b in built:
        h = heads[b.kind]
        if h is None:
            continue
        if h.size == len(b.body) and h.etag == b.md5:
            recovered.add(b.kind)
            continue
        raise Refused(f"{store.bucket}/{b.key} already exists with no manifest line and does not "
                      "match this build (size or ETag); never overwritten. Resolve by hand")

    lines = []
    for b in built:
        if b.kind in recovered:
            etag, head = heads[b.kind].etag, heads[b.kind]
            log.warning("%s/%s already present and matching; recording it (recovered)", store.bucket, b.key)
        else:
            etag = store.put(b.key, b.body, CONTENT_TYPE)
            head = store.head(b.key)
            if head is None or head.size != len(b.body):
                raise Refused(f"{store.bucket}/{b.key}: HEAD after PUT shows "
                              f"{'nothing' if head is None else f'{head.size} bytes'}, "
                              f"expected {len(b.body)}; no manifest line written")
        lines.append(manifest_line(b, data, revision, store.bucket, etag, head, b.kind in recovered))
        log.info("%s/%s: %d rows, %d bytes, sha256 %s, etag %s md5", store.bucket, b.key, b.rows,
                 len(b.body), b.sha256, "==" if etag == b.md5 else "!=")
    append_manifest(manifest_path, lines)
    log.info("week %d r%d archived to storage: %s; manifest %s (commit it)",
             week, revision, data.counts, manifest_path.relative_to(REPO_ROOT)
             if manifest_path.is_relative_to(REPO_ROOT) else manifest_path)
    if data.counts["unsettled"]:
        log.warning("%d markets unsettled — rerun a week later with --revision %d",
                    data.counts["unsettled"], revision + 1)
    return EXIT_OK
