"""The props archive's storage mode (docs/props_archive_storage.md).

Stubbed Kalshi, stubbed schedule, an in-memory store, a temp manifest. Never
reaches Kalshi, Supabase Storage or the database.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from core import s3
from core.s3 import ObjectInfo, S3Client, S3Config, S3Error
from jobs import archive_props_storage as st
from sports.nfl.schedule import Game
from venues.kalshi.client import Candle, Quote, Trade

UTC = timezone.utc
K1 = datetime(2026, 10, 16, 0, 15, tzinfo=UTC)
K2 = datetime(2026, 10, 20, 0, 15, tzinfo=UTC)
NOW = K2 + timedelta(days=10)
CUTOFF = datetime(2026, 9, 5, tzinfo=UTC)


def game(gid: str, kickoff: datetime, week: int = 6) -> Game:
    return Game(game_id=gid, season=2026, week=week, game_type="REG", away="SEA", home="DEN",
                kickoff=kickoff, away_rest=None, home_rest=None)


class Schedule:
    def __init__(self, games: list[Game], events: dict[str, str]) -> None:
        self.games = games
        self._events = events
        self._by_id = {g.game_id: g for g in games}

    def for_event(self, event_ticker: str) -> Game | None:
        gid = self._events.get(event_ticker)
        return self._by_id.get(gid) if gid else None


def quote(ticker: str, event: str, settled: datetime | None = K2 + timedelta(days=1)) -> Quote:
    return Quote(ticker=ticker, event_ticker=event, status="finalized" if settled else "closed",
                 yes_bid=None, yes_ask=None, no_bid=None, no_ask=None, yes_ask_size=None,
                 no_ask_size=None, result="yes" if settled else "",
                 settlement_value=Decimal("1.00") if settled else None, settlement_ts=settled,
                 expected_expiration=None, close_time=None, fetched_at=NOW,
                 floor_strike=Decimal("49.5"), title=f"{ticker} title")


class Kalshi:
    """Two games, one market per series per game; candles and prints in the window."""

    def __init__(self, extra_print: bool = False, cutoff: datetime = CUTOFF,
                 unsettled: bool = False) -> None:
        self.cutoff = cutoff
        self.extra_print = extra_print
        self.unsettled = unsettled
        self.calls: list[str] = []

    def historical_cutoff(self) -> datetime:
        return self.cutoff

    def markets(self, *, series_ticker: str) -> list[Quote]:
        self.calls.append(f"markets {series_ticker}")
        out = [quote(f"{series_ticker}-A", "EV-A"),
               quote(f"{series_ticker}-B", "EV-B",
                     settled=None if self.unsettled and series_ticker == "KXNFLRECYDS" else K2 + timedelta(days=1)),
               quote(f"{series_ticker}-X", "EV-OTHER-WEEK")]
        return out

    def historical_markets(self, *, series_ticker: str) -> list[Quote]:
        return []

    def candles(self, series, ticker, start, end, period, *, settled_at=None) -> list[Candle]:
        return [Candle(end=end - timedelta(minutes=m), yes_bid_close=Decimal("0.4100"),
                       yes_ask_close=Decimal("0.4300"), trade_low=None, trade_high=None,
                       trade_close=Decimal("0.42"), volume=Decimal("10.00"), open_interest=Decimal("5"))
                for m in (0, 1, 2, 200)]

    def trades(self, ticker, start, end, *, settled_at=None) -> list[Trade]:
        out = [Trade(at=end - timedelta(minutes=5), yes_price=Decimal("0.42"), count=Decimal("3.00"),
                     trade_id=f"{ticker}-t1", taker_side="yes"),
               Trade(at=end - timedelta(minutes=9), yes_price=Decimal("0.40"), count=Decimal("1.00"),
                     trade_id=None, taker_side="no")]
        if self.extra_print:
            out.append(Trade(at=end - timedelta(minutes=1), yes_price=Decimal("0.5"),
                             count=Decimal("1"), trade_id=f"{ticker}-t2", taker_side="yes"))
        return out


class MemStore:
    bucket = "nfl-props-archive"

    def __init__(self, etag_is_md5: bool = True, short_head: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.etag_is_md5 = etag_is_md5
        self.short_head = short_head

    def _etag(self, body: bytes) -> str:
        return hashlib.md5(body).hexdigest() if self.etag_is_md5 else "opaque-etag"

    def head(self, key: str) -> ObjectInfo | None:
        body = self.objects.get(key)
        if body is None:
            return None
        return ObjectInfo(size=len(body) - (1 if self.short_head else 0), etag=self._etag(body))

    def put(self, key: str, body: bytes, content_type: str) -> str:
        assert key not in self.objects, "overwrote an object"
        self.objects[key] = body
        self.puts.append(key)
        return self._etag(body)


SCHEDULE = Schedule([game("2026_06_SEA_DEN", K1), game("2026_06_WAS_SF", K2)],
                    {"EV-A": "2026_06_SEA_DEN", "EV-B": "2026_06_WAS_SF"})


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.manifest = Path(self.tmp.name) / "manifest.jsonl"
        self.store = MemStore()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_week(self, week: int = 6, revision: int = 1, client: Kalshi | None = None,
                 schedule: Schedule = SCHEDULE, now: datetime = NOW, store: MemStore | None = None) -> int:
        return st.run(week, revision, schedule=schedule, client=client or Kalshi(),
                      store=store or self.store, manifest_path=self.manifest, now=now)

    def lines(self) -> list[dict]:
        return st.load_manifest(self.manifest)


class FirstRun(Base):
    def test_uploads_four_objects_and_writes_four_manifest_lines(self) -> None:
        self.assertEqual(self.run_week(), st.EXIT_OK)
        self.assertEqual(sorted(self.store.puts), sorted(
            f"2026/week-06/r1/{k}.jsonl.gz" for k in st.KINDS))
        lines = self.lines()
        self.assertEqual([l["kind"] for l in lines], list(st.KINDS))
        for l in lines:
            body = self.store.objects[l["path"]]
            self.assertEqual(l["sha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual((l["bytes"], l["head_bytes"]), (len(body), len(body)))
            self.assertTrue(l["etag_is_md5"])
            self.assertEqual((l["week"], l["revision"], l["bucket"]), (6, 1, "nfl-props-archive"))

    def test_counts_are_the_postgres_jobs_counts_plus_id_less_prints(self) -> None:
        self.run_week()
        c = self.lines()[0]["week_counts"]
        # 3 series x 2 games; the other week's market is skipped
        self.assertEqual((c["markets"], c["settled"], c["unsettled"]), (6, 6, 0))
        self.assertEqual(c["candles"], 6 * 4)   # 3 in the window + 1 latest before it
        self.assertEqual((c["prints"], c["prints_without_id"]), (12, 6))
        self.assertEqual((c["games"], c["games_pending"]), (2, 0))

    def test_rows_mirror_the_postgres_columns_without_the_tier(self) -> None:
        self.run_week()
        candles = [json.loads(x) for x in gzip.decompress(
            self.store.objects["2026/week-06/r1/candles.jsonl.gz"]).decode().splitlines()]
        self.assertEqual(set(candles[0]), {"ticker", "period_minutes", "end_period_ts", "yes_bid_close",
                                           "yes_ask_close", "trade_close", "volume", "open_interest"})
        self.assertEqual(candles[0]["yes_bid_close"], "0.4100")  # Kalshi's string, no float
        self.assertNotIn("source", candles[0])
        self.assertEqual(self.lines()[0]["tiers"], {"live": 6, "historical": 0})

    def test_the_tier_does_not_change_the_bytes(self) -> None:
        self.run_week()
        moved = Kalshi(cutoff=NOW)  # every market now in the historical tier
        a = st.build(st.collect(6, SCHEDULE, Kalshi(), NOW), 1)
        b = st.build(st.collect(6, SCHEDULE, moved, NOW), 1)
        self.assertEqual([x.sha256 for x in a], [x.sha256 for x in b])


class Rerun(Base):
    def test_identical_rerun_is_a_no_op(self) -> None:
        self.run_week()
        before = self.manifest.read_bytes()
        self.store.puts.clear()
        self.assertEqual(self.run_week(), st.EXIT_OK)
        self.assertEqual(self.store.puts, [])
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_a_mismatch_refuses_loudly_and_uploads_nothing(self) -> None:
        self.run_week()
        before = self.manifest.read_bytes()
        self.store.puts.clear()
        logging.disable(logging.NOTSET)  # tests/__init__ silences logs; this one is the behaviour
        self.addCleanup(logging.disable, logging.CRITICAL)
        with self.assertLogs("valemont.archive_props_storage", "ERROR") as logs:
            code = self.run_week(client=Kalshi(extra_print=True))
        self.assertEqual(code, st.EXIT_REFUSED)
        self.assertIn("REFUSED", logs.output[0])
        self.assertIn("prints", logs.output[0])
        self.assertEqual(self.store.puts, [])
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_recorded_but_missing_from_storage_refuses(self) -> None:
        self.run_week()
        del self.store.objects["2026/week-06/r1/prints.jsonl.gz"]
        self.assertEqual(self.run_week(), st.EXIT_REFUSED)


class Refusals(Base):
    def test_a_week_with_a_pending_game_is_refused_whole_before_any_fetch(self) -> None:
        client = Kalshi()
        code = self.run_week(client=client, now=K2 + timedelta(hours=4))
        self.assertEqual(code, st.EXIT_REFUSED)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.store.puts, [])
        self.assertFalse(self.manifest.exists())

    def test_week_5_and_out_of_range_weeks_stay_out(self) -> None:
        for week in (5, 19):
            self.assertEqual(self.run_week(week=week), st.EXIT_REFUSED)
        self.assertEqual(self.store.puts, [])

    def test_existing_object_without_a_manifest_line_is_never_overwritten(self) -> None:
        self.store.objects["2026/week-06/r1/candles.jsonl.gz"] = b"something else"
        self.assertEqual(self.run_week(), st.EXIT_REFUSED)
        self.assertEqual(self.store.puts, [])  # HEAD all four before any PUT
        self.assertFalse(self.manifest.exists())

    def test_a_matching_stray_object_is_recorded_as_recovered(self) -> None:
        built = {b.kind: b for b in st.build(st.collect(6, SCHEDULE, Kalshi(), NOW), 1)}
        self.store.objects[built["markets"].key] = built["markets"].body
        self.assertEqual(self.run_week(), st.EXIT_OK)
        self.assertNotIn(built["markets"].key, self.store.puts)
        rec = {l["kind"]: l.get("recovered", False) for l in self.lines()}
        self.assertEqual(rec, {"markets": True, "settlements": False, "candles": False, "prints": False})

    def test_a_stray_object_is_not_recovered_when_the_etag_is_not_an_md5(self) -> None:
        store = MemStore(etag_is_md5=False)
        built = st.build(st.collect(6, SCHEDULE, Kalshi(), NOW), 1)
        store.objects[built[0].key] = built[0].body
        self.assertEqual(self.run_week(store=store), st.EXIT_REFUSED)

    def test_head_after_put_with_the_wrong_size_writes_no_manifest_line(self) -> None:
        self.assertEqual(self.run_week(store=MemStore(short_head=True)), st.EXIT_REFUSED)
        self.assertFalse(self.manifest.exists())

    def test_opaque_etag_is_recorded_as_size_only(self) -> None:
        self.assertEqual(self.run_week(store=MemStore(etag_is_md5=False)), st.EXIT_OK)
        self.assertFalse(any(l["etag_is_md5"] for l in self.lines()))


class Revisions(Base):
    def test_revision_2_refused_when_r1_was_fully_settled(self) -> None:
        self.run_week()
        client = Kalshi()
        self.assertEqual(self.run_week(revision=2, client=client), st.EXIT_REFUSED)
        self.assertEqual(client.calls, [])  # checked before the fetch

    def test_revision_2_refused_without_r1(self) -> None:
        self.assertEqual(self.run_week(revision=2), st.EXIT_REFUSED)

    def test_revision_2_after_unsettled_r1_writes_a_new_folder_and_keeps_r1(self) -> None:
        self.run_week(client=Kalshi(unsettled=True))
        self.assertEqual(self.lines()[0]["week_counts"]["unsettled"], 1)
        r1 = dict(self.store.objects)
        self.assertEqual(self.run_week(revision=2), st.EXIT_OK)
        for key, body in r1.items():
            self.assertEqual(self.store.objects[key], body)
        self.assertEqual(sorted({l["revision"] for l in self.lines()}), [1, 2])
        self.assertTrue(all(k in self.store.objects for k in
                            (f"2026/week-06/r2/{kind}.jsonl.gz" for kind in st.KINDS)))


class Serialization(unittest.TestCase):
    def test_same_rows_same_bytes_and_no_timestamp_in_the_header(self) -> None:
        rows = [{"b": "1", "a": None}, {"a": "x", "b": "2"}]
        one, two = st.serialize(rows), st.serialize(list(rows))
        self.assertEqual(one, two)
        self.assertEqual(one[4:8], b"\x00\x00\x00\x00")  # gzip MTIME
        self.assertEqual(gzip.decompress(one), b'{"a":null,"b":"1"}\n{"a":"x","b":"2"}\n')

    def test_naive_timestamps_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            st._ts(datetime(2026, 10, 1))


class SigV4(unittest.TestCase):
    def test_aws_documented_get_object_example(self) -> None:
        # AWS S3 docs, "Signature Calculations for the Authorization Header",
        # example GET Object (examplebucket/test.txt, Range bytes=0-9).
        headers = s3.sign(
            "GET", "https://examplebucket.s3.amazonaws.com/test.txt", {"Range": "bytes=0-9"},
            hashlib.sha256(b"").hexdigest(),
            access_key_id="AKIAIOSFODNN7EXAMPLE",
            secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            region="us-east-1", now=datetime(2013, 5, 24, tzinfo=UTC))
        self.assertEqual(
            headers["Authorization"],
            "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
            "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
            "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


ENV = {"PROPS_ARCHIVE_S3_ENDPOINT": "https://ref.storage.supabase.co/storage/v1/s3",
       "PROPS_ARCHIVE_S3_REGION": "us-east-1", "PROPS_ARCHIVE_S3_ACCESS_KEY_ID": "AKID",
       "PROPS_ARCHIVE_S3_SECRET_ACCESS_KEY": "SECRET-VALUE"}


class Client(unittest.TestCase):
    def test_from_env_names_missing_keys_and_never_shows_a_value(self) -> None:
        with self.assertRaises(S3Error) as ctx:
            S3Config.from_env({"PROPS_ARCHIVE_S3_SECRET_ACCESS_KEY": "SECRET-VALUE"})
        self.assertIn("PROPS_ARCHIVE_S3_ENDPOINT", str(ctx.exception))
        self.assertNotIn("SECRET-VALUE", str(ctx.exception))
        cfg = S3Config.from_env(ENV)
        self.assertEqual(cfg.bucket, "nfl-props-archive")
        self.assertNotIn("SECRET-VALUE", repr(cfg))
        self.assertNotIn("AKID", repr(cfg))

    def test_put_signs_the_real_payload_hash_path_style(self) -> None:
        seen = []

        def transport(method, url, headers, body):
            seen.append((method, url, dict(headers), body))
            return 200, {"etag": '"abc"'}, b""

        client = S3Client(S3Config.from_env(ENV), transport=transport,
                          clock=lambda: datetime(2026, 11, 4, tzinfo=UTC))
        self.assertEqual(client.put("2026/week-06/r1/x.jsonl.gz", b"data", "application/gzip"), "abc")
        method, url, headers, body = seen[0]
        self.assertEqual((method, body), ("PUT", b"data"))
        self.assertEqual(url, "https://ref.storage.supabase.co/storage/v1/s3/nfl-props-archive/"
                              "2026/week-06/r1/x.jsonl.gz")
        self.assertEqual(headers["x-amz-content-sha256"], hashlib.sha256(b"data").hexdigest())
        self.assertNotIn("SECRET-VALUE", json.dumps(headers))

    def test_head_404_is_none_and_errors_carry_no_secret(self) -> None:
        client = S3Client(S3Config.from_env(ENV), transport=lambda *a: (404, {}, b""))
        self.assertIsNone(client.head("k"))
        client = S3Client(S3Config.from_env(ENV), transport=lambda *a: (403, {}, b"denied"))
        with self.assertRaises(S3Error) as ctx:
            client.head("k")
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertNotIn("supabase.co", str(ctx.exception))

    def test_bucket_head_tells_missing_bucket_from_rejected_keys(self) -> None:
        urls = []

        def answer(status):
            def transport(method, url, headers, body):
                urls.append((method, url))
                return status, {}, b""
            return transport

        self.assertTrue(S3Client(S3Config.from_env(ENV), transport=answer(200)).bucket_exists())
        self.assertEqual(urls[0], ("HEAD", "https://ref.storage.supabase.co/storage/v1/s3/nfl-props-archive"))
        self.assertFalse(S3Client(S3Config.from_env(ENV), transport=answer(404)).bucket_exists())
        with self.assertRaises(S3Error) as ctx:
            S3Client(S3Config.from_env(ENV), transport=answer(403)).bucket_exists()
        self.assertIn("rejected", str(ctx.exception))

    def test_the_client_cannot_read_an_object_back(self) -> None:
        # Store-only (preregistration §8.1): no GET, no LIST, no DELETE.
        for verb in ("get", "get_object", "list", "delete"):
            self.assertFalse(hasattr(S3Client, verb), verb)

    def test_unsafe_keys_are_refused(self) -> None:
        client = S3Client(S3Config.from_env(ENV), transport=lambda *a: (200, {}, b""))
        for key in ("", "/abs", "a/../b"):
            with self.assertRaises(S3Error):
                client.head(key)


if __name__ == "__main__":
    unittest.main()
