"""Phase 4.4: the Parlay Generator's agents, the shared slot re-quote, and the stamp guard's image copy.

docs/preregistration_parlays.md v2 (P2), docs/parlay_generator_plan.md §4.4. The agents run here as
`_test` (is_test) on the stub ledger; Kalshi is a fake; nothing reaches a database or the network."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

from adapters import parlays as ap
from consensus import correlation as cr
from consensus import parlay_legs as pl
from consensus import parlay_prereg
from consensus import parlay_slot as ps
from consensus import parlay_ticket as pt
from core import paths
from core import preregistration as pr
from core.agent import CloseUnavailable
from core.jobs import JobSkipped
from core.ledger import Leg
from tests.support import LedgerTestCase, pending
from venues.kalshi import board as kb
from venues.kalshi.client import Candle, Quote as KQuote

UTC = timezone.utc
#: 10:45 Chicago on Sun Nov 22 (CST, UTC-6).
COMMIT = datetime(2026, 11, 22, 16, 45, tzinfo=UTC)
REQUOTED = COMMIT - timedelta(minutes=12)
KICK = COMMIT + timedelta(hours=3)
EXPIRES = KICK + timedelta(hours=4)
T = cr.load_table()


def market(i: int, *, ask: str = "0.75", sport: str = "Basketball", starts: datetime = KICK) -> pl.Market:
    return pl.Market(market_ticker=f"KXNBAPTS-G{i}-P{i}-10", event_ticker=f"KXNBAPTS-G{i}", game_ticker=f"G{i}",
                     series_ticker="KXNBAPTS", sport=sport, league="nba", starts_at=starts + timedelta(minutes=i),
                     market_kind="prop", stat="points", subject=f"Player {i}", team="AAA", line=D("9.5"), tier="kalshi",
                     p_yes=D("0.76"), yes_bid=D(ask) - D("0.01"), yes_ask=D(ask), payout="binary", combo_eligible=True,
                     combo_yes_only=False, combo_size_max=None, mutually_exclusive=False, label=f"Player {i}: 10+")


def slot(n: int = 6, *, quoted_at: datetime = REQUOTED, **kw: Any) -> ps.SlotQuotes:
    ms = [market(i, **kw) for i in range(n)]
    quotes = {m.market_ticker: pl.Quote(m.yes_bid, m.yes_ask, quoted_at, EXPIRES + timedelta(minutes=i))
              for i, m in enumerate(ms)}
    rq = pl.Requote(quotes=quotes, events_asked=n, calls=n, seconds=60.0, pace=0.1)
    return ps.SlotQuotes(key=ps.slot_key(COMMIT), markets=pl.with_fresh_p(ms, quotes), quotes=quotes, cut={},
                         requote=rq, started_at=REQUOTED, finished_at=REQUOTED + timedelta(minutes=1), read_seconds=1.0)


class TestParlay(ap.ParlayAgent):
    """A parlay tier running as the `_test` harness agent (is_test)."""

    slug: ClassVar[str] = "_test"
    tier: ClassVar[str] = "safer"


def agent(data: ps.SlotQuotes | None, *, tier: str = "safer", at: datetime = COMMIT,
          db_now: datetime = COMMIT, guard=lambda: None, client: Any = None,
          starts: Any = None) -> TestParlay:
    a = type(f"Test{tier}", (TestParlay,), {"tier": tier})(
        client=client or FakeClient(), clock=lambda: at, slots=lambda key: data, guard=guard,
        db_now=lambda: db_now, rho=T, draws=2000, starts=starts or (lambda events: {}))
    return a


class FakeClient:
    def __init__(self, settled: dict[str, str | None] | None = None, candles: dict[str, list[Candle]] | None = None):
        self.settled = settled or {}
        self.candle_map = candles or {}
        self.calls: list[tuple] = []

    def market(self, ticker: str) -> KQuote:
        self.calls.append(("market", ticker))
        v = self.settled.get(ticker)
        return KQuote(ticker=ticker, event_ticker="", status="finalized" if v is not None else "open", yes_bid=None,
                      yes_ask=None, no_bid=None, no_ask=None, yes_ask_size=None, no_ask_size=None,
                      result="", settlement_value=None if v is None else D(v), settlement_ts=None,
                      expected_expiration=None, close_time=None, fetched_at=COMMIT)

    def candles(self, series: str, ticker: str, start: datetime, end: datetime, period: int, **kw: Any) -> list[Candle]:
        self.calls.append(("candles", ticker, start, end))
        return [c for c in self.candle_map.get(ticker, []) if start <= c.end <= end]


def candle(end: datetime, bid: str | None, ask: str | None) -> Candle:
    return Candle(end=end, yes_bid_close=None if bid is None else D(bid), yes_ask_close=None if ask is None else D(ask),
                  trade_low=None, trade_high=None, trade_close=None, volume=D(0), open_interest=None)


# --------------------------------------------------------------------------- the shared re-quote
class SharedRequote(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(kb.SLOT_REQUOTE.clear)
        self.addCleanup(ps._STORE.clear)

    def test_one_requote_per_slot_serves_all_three_tiers(self):
        calls = []

        def requote(events):
            calls.append(list(events))
            self.assertTrue(kb.SLOT_REQUOTE.is_set())  # the refresh yields for the whole re-quote
            return slot().requote

        rows = [{k: v for k, v in market(i).__dict__.items()} for i in range(6)]
        data = ps.requote_slot(now=lambda: REQUOTED, read=lambda: (rows, {}), requote=requote)
        self.assertFalse(kb.SLOT_REQUOTE.is_set())
        self.assertEqual(len(calls), 1)
        self.assertIs(ps.get(ps.slot_key(COMMIT)), data)
        seen = [agent(None, tier=t).observe() for t in ("safer", "mid", "lotto")]
        for t, obs in zip(("safer", "mid", "lotto"), seen):
            a = agent(None, tier=t)
            a.slots = ps.get
            self.assertIs(a.observe().data, data)
        self.assertEqual(len(calls), 1)  # no tier calls Kalshi itself

    def test_the_flag_is_cleared_even_when_the_requote_fails(self):
        def boom(events):
            raise RuntimeError("kalshi down")
        with self.assertRaises(RuntimeError):
            ps.requote_slot(now=lambda: REQUOTED, read=lambda: ([], {}), requote=boom)
        self.assertFalse(kb.SLOT_REQUOTE.is_set())

    def test_the_gate_roster_production_and_an_enabled_parlay_agent(self):
        self.assertIn("ROSTER", ps.gate_open({}, enabled=lambda s: True))
        self.assertIn("enabled", ps.gate_open({"ROSTER": "production"}, enabled=lambda s: False))
        self.assertIsNone(ps.gate_open({"ROSTER": "production"}, enabled=lambda s: s == "parlay_mid"))

    def test_the_job_skips_with_a_note_while_the_gate_is_closed(self):
        with mock.patch.dict(os.environ, {"ROSTER": ""}), self.assertRaisesRegex(JobSkipped, "ROSTER"):
            ps.requote_job()

    def test_slot_keys_by_chicago_date_and_half(self):
        self.assertEqual(ps.slot_key(COMMIT), (date(2026, 11, 22), "am"))
        self.assertEqual(ps.slot_key(datetime(2026, 11, 22, 22, 45, tzinfo=UTC)), (date(2026, 11, 22), "pm"))
        # 16:45 Chicago on Nov 22 is 22:45 UTC; 00:30 UTC Nov 23 is still Nov 22 in Chicago.
        self.assertEqual(ps.slot_key(datetime(2026, 11, 23, 0, 30, tzinfo=UTC)), (date(2026, 11, 22), "pm"))

    def test_the_system_job_runs_at_33_never_at_boot_with_five_minutes_grace(self):
        from core.system_jobs import build_system_jobs
        (job,) = [j for j in build_system_jobs() if j.name == ps.JOB]
        self.assertFalse(job.run_at_boot)
        self.assertEqual(job.misfire_grace, 300)
        self.assertIn("minute='33'", str(job.trigger))
        self.assertIn("hour='10,16'", str(job.trigger))


# --------------------------------------------------------------------------- the agent
class Thesis(LedgerTestCase):
    def test_no_requote_for_the_slot_is_an_empty_slot_bound_by_fresh(self):
        a = agent(None)
        a._tick_run_id = 1
        self.assertIsNone(a.form_thesis(a.observe()))
        (kind, detail), = [e for e in self.ledger.events() if e[0] == "gate_evaluated"]
        self.assertEqual(detail["binding"], "fresh")

    def test_a_leg_older_than_20_minutes_at_selection_is_dropped(self):
        a = agent(slot(quoted_at=COMMIT - timedelta(minutes=21)))
        a._tick_run_id = 1
        self.assertIsNone(a.form_thesis(a.observe()))
        # The sport is still recorded: empty, bound by rule 2 (§2: never skipped silently).
        bindings = [(d["sport"], d.get("binding")) for k, d in self.ledger.events() if k == "gate_evaluated"]
        self.assertEqual(bindings, [("Basketball", "rules 1-4: fresh")])

    def test_every_scope_is_recorded_ticket_or_empty(self):
        a = agent(slot())
        a._tick_run_id = 1
        th = a.form_thesis(a.observe())
        self.assertEqual(len(th.tickets), 1)
        evs = [d for k, d in self.ledger.events() if k == "gate_evaluated"]
        self.assertEqual([(d["scope"], d["sport"]) for d in evs], [("sport", "Basketball")])
        self.assertEqual(evs[0]["legs"], len(th.tickets[0].legs))


class Commit(LedgerTestCase):
    def thesis(self, data: ps.SlotQuotes, **kw: Any):
        a = agent(data, **kw)
        a._tick_run_id = 1
        return a, a.form_thesis(a.observe())

    def test_nothing_commits_unless_v2_is_stamped(self):
        def unstamped():
            raise pr.PreregistrationMissing("not stamped")
        a, th = self.thesis(slot(), guard=unstamped)
        with self.assertRaises(pr.PreregistrationMissing):
            a.build_commitment(th)

    def test_a_ticket_with_a_started_game_by_the_database_clock_is_refused(self):
        a, th = self.thesis(slot(), db_now=KICK + timedelta(minutes=1))  # one leg's game has started
        self.assertEqual(a.build_commitment(th), [])
        refused = [d["refused"] for k, d in self.ledger.events() if k == "gate_evaluated" and "refused" in d]
        self.assertTrue(refused and refused[0].startswith("game started by the database clock"))

    def test_a_leg_over_20_minutes_old_at_commit_refuses_its_ticket(self):
        a, th = self.thesis(slot(quoted_at=COMMIT - timedelta(minutes=19)))
        a.clock = lambda: COMMIT + timedelta(minutes=2)  # 21 minutes at commit
        self.assertEqual(a.build_commitment(th), [])

    def test_the_proposal(self):
        a, th = self.thesis(slot())
        (p,) = a.build_commitment(th)
        t = th.tickets[0]
        self.assertEqual(p.kind, "event_contract")
        self.assertEqual([(l.subject, l.market, l.line, l.direction, l.size) for l in p.legs],
                         [(c.m.market_ticker, "KXNBAPTS", c.ask, c.leg.side, 100) for c in t.legs])
        self.assertEqual(p.closes_at, max(c.m.starts_at for c in t.legs))
        self.assertEqual(p.resolves_after, max(c.leg.quote.expires for c in t.legs))
        self.assertEqual(p.quote_fetched_at, REQUOTED)
        pay = p.payload
        self.assertEqual((pay["rule_version"], pay["invalidation"], pay["expected_quote"]), ("P2.1", "0", None))
        self.assertEqual(D(pay["capital_at_risk"]), (t.priced.scored_price + t.priced.taker_fee) * 100)
        self.assertEqual(pay["oldest_leg_age_s_at_commit"], 720.0)
        self.assertEqual(set(pay["kickoffs"]), {c.m.market_ticker for c in t.legs})
        self.assertTrue(D("0.15") <= D(pay["scored_price"]) <= D("0.40"))

    def test_a_lotto_slate_commits_its_cross_sport_ticket_beside_a_per_sport_one_sharing_legs(self):
        # Owner, Nov 20 (option D): core refuses the same ticket twice, not two tickets sharing legs.
        ms = [market(i) for i in range(14)] + [market(100 + i, sport="Hockey") for i in range(2)]
        quotes = {m.market_ticker: pl.Quote(m.yes_bid, m.yes_ask, REQUOTED, EXPIRES) for m in ms}
        data = ps.SlotQuotes(key=ps.slot_key(COMMIT), markets=pl.with_fresh_p(ms, quotes), quotes=quotes, cut={},
                             requote=pl.Requote(quotes=quotes), started_at=REQUOTED, finished_at=REQUOTED,
                             read_seconds=1.0)
        out = agent(data, tier="lotto").run_once()
        self.assertEqual((out.status, len(out.commitment_ids)), ("ok", 2), out.error)
        legs = [{l.subject for l in c.kwargs["legs"]} for c in self.ledger.named("commit")]
        self.assertGreater(len(legs[0] & legs[1]), 0)  # they do share legs
        self.assertEqual([c.kwargs["payload"]["scope"] for c in self.ledger.named("commit")], ["sport", "cross_sport"])

    def test_a_full_run_commits_a_slate_as_the_test_agent(self):
        a = agent(slot())
        out = a.run_once()
        self.assertEqual(out.status, "ok", out.error)
        self.assertEqual(len(out.commitment_ids), 1)
        (commit,) = self.ledger.named("commit")
        self.assertEqual(commit.kwargs["kind"], "event_contract")
        self.assertIn("quote_provenance", commit.kwargs["payload"])


class ResolveAndClose(LedgerTestCase):
    LEGS = (Leg("A", "KXNBAPTS", D("0.75"), "yes", 100), Leg("B", "KXNBAPTS", D("0.80"), "no", 100))
    PAY = {"capital_at_risk": "61.00", "naive_p": "0.55",
           "kickoffs": {"A": KICK.isoformat(), "B": (KICK + timedelta(hours=1)).isoformat()}}

    def resolve(self, settled):
        a = agent(None, client=FakeClient(settled))
        return a.resolve(pending(1, legs=self.LEGS, payload=self.PAY))

    def test_hit_miss_partial_and_not_yet(self):
        self.assertEqual(self.resolve({"A": "1", "B": "0"}).outcome, "hit")
        miss = self.resolve({"A": "0", "B": "0"})
        self.assertEqual((miss.outcome, miss.pnl), ("miss", D(-1)))
        part = self.resolve({"A": "1", "B": "0.5"})
        self.assertEqual(part.outcome, "partial")
        self.assertEqual(part.pnl, (D("50") - D("61")) / D("61"))
        self.assertEqual([lo.outcome for lo in part.leg_outcomes], ["hit", "settled"])
        self.assertIsNone(self.resolve({"A": "1", "B": None}))  # every leg settles first

    def test_the_clv_proxy_two_sided_mids_at_or_before_each_kickoff(self):
        k_a, k_b = KICK, KICK + timedelta(hours=1)
        cl = FakeClient(candles={"A": [candle(k_a - timedelta(minutes=2), "0.70", "0.72"),
                                       candle(k_a + timedelta(minutes=1), "0.10", "0.12")],  # after kickoff: ignored
                                 "B": [candle(k_b, "0.18", "0.20")]})
        a = agent(None, client=cl, at=k_b + timedelta(minutes=6))
        close = a.capture_close(pending(1, legs=self.LEGS, payload=self.PAY, closes_at=k_b))
        self.assertEqual(close.price, D("0.71") * D("0.81"))  # YES 0.71; NO 1 - 0.19
        self.assertEqual(close.entry_price, D("0.55"))
        self.assertEqual(close.detail["label"], "CLV (proxy)")

    def test_not_before_the_latest_kickoff_and_a_leg_without_a_two_sided_quote_is_missed(self):
        k_b = KICK + timedelta(hours=1)
        early = agent(None, at=k_b + timedelta(minutes=4))
        self.assertIsNone(early.capture_close(pending(1, legs=self.LEGS, payload=self.PAY, closes_at=k_b)))
        cl = FakeClient(candles={"A": [candle(KICK - timedelta(minutes=1), "0.70", "0.72")],
                                 "B": [candle(k_b - timedelta(minutes=1), None, "0.20")]})  # one-sided
        a = agent(None, client=cl, at=k_b + timedelta(minutes=6))
        with self.assertRaisesRegex(CloseUnavailable, r"no two-sided quote .* before kickoff: B"):
            a.capture_close(pending(1, legs=self.LEGS, payload=self.PAY, closes_at=k_b))


class P21Close(LedgerTestCase):
    """P2.1 §5 and §6 (owner, Nov 24). The ResolveAndClose cases above have no rule_version: they
    are P2 tickets (cohort 1) and keep P2's close, unchanged."""

    LEGS = ResolveAndClose.LEGS
    K_A, K_B = KICK, KICK + timedelta(hours=1)
    PAY = {**ResolveAndClose.PAY, "rule_version": "P2.1",
           "legs": [{"market_ticker": "A", "event_ticker": "EV-A"}, {"market_ticker": "B", "event_ticker": "EV-B"}]}

    def close(self, candles, *, at=None, starts=None, payload=None):
        a = agent(None, client=FakeClient(candles=candles), at=at or self.K_B + timedelta(minutes=6), starts=starts)
        return a.capture_close(pending(1, legs=self.LEGS, payload=payload or self.PAY, closes_at=self.K_B)), a

    def test_a_standing_quote_27_minutes_before_kickoff_is_the_close_with_its_age(self):
        candles = {"A": [candle(self.K_A - timedelta(minutes=27), "0.25", "0.26")],
                   "B": [candle(self.K_B - timedelta(minutes=8), "0.18", "0.20")]}
        close, _ = self.close(candles)
        self.assertEqual(close.price, D("0.255") * D("0.81"))
        self.assertEqual([x["age_min"] for x in close.detail["legs"]], [27.0, 8.0])
        self.assertEqual((close.detail["age_min_median"], close.detail["age_min_max"]), (17.5, 27.0))
        self.assertEqual(close.detail["rule_version"], "P2.1")
        # The same candles under P2's rule (cohort 1): missed, unchanged.
        with self.assertRaisesRegex(CloseUnavailable, r"no two-sided quote .* before kickoff: A"):
            self.close(candles, payload=ResolveAndClose.PAY)

    def test_the_last_candle_must_be_two_sided_an_earlier_one_does_not_count(self):
        candles = {"A": [candle(self.K_A - timedelta(minutes=50), "0.25", "0.26"),
                         candle(self.K_A, None, "0.06")],  # one-sided at kickoff: the bid left
                   "B": [candle(self.K_B - timedelta(minutes=1), "0.18", "0.20")]}
        with self.assertRaisesRegex(CloseUnavailable, r"is not two-sided: A"):
            self.close(candles)

    def test_sixty_minutes_inclusive_and_nothing_older(self):
        ok = {"A": [candle(self.K_A - timedelta(minutes=60), "0.25", "0.26")],
              "B": [candle(self.K_B, "0.18", "0.20")]}
        close, _ = self.close(ok)
        self.assertEqual(close.detail["legs"][0]["age_min"], 60.0)
        old = {"A": [candle(self.K_A - timedelta(minutes=61), "0.25", "0.26")], "B": ok["B"]}
        with self.assertRaisesRegex(CloseUnavailable, r"no candle in the 1:00:00 before kickoff: A"):
            self.close(old)

    def test_a_postponed_leg_is_closed_at_its_new_start_and_not_before(self):
        later = self.K_B + timedelta(days=1)
        starts = lambda events: {"EV-B": later, "EV-A": self.K_A - timedelta(hours=2)}  # A's earlier: ignored
        candles = {"A": [candle(self.K_A - timedelta(minutes=2), "0.70", "0.72")],
                   "B": [candle(self.K_B - timedelta(minutes=1), "0.50", "0.52"),       # the original day
                         candle(later - timedelta(minutes=3), "0.18", "0.20")]}
        not_yet, _ = self.close(candles, starts=starts)  # 6 minutes after the original kickoff
        self.assertIsNone(not_yet)
        close, a = self.close(candles, starts=starts, at=later + timedelta(minutes=6))
        a_leg, b_leg = close.detail["legs"]
        self.assertEqual((a_leg["kickoff"], a_leg["postponed"]), (self.K_A.isoformat(), False))
        self.assertEqual((b_leg["kickoff"], b_leg["kickoff_at_commit"], b_leg["postponed"], b_leg["age_min"]),
                         (later.isoformat(), self.K_B.isoformat(), True, 3.0))
        self.assertEqual(close.price, D("0.71") * D("0.81"))
        self.assertIn(("candles", "B", later - timedelta(minutes=60), later), a.client.calls)

    def test_resolves_after_is_never_before_closes_at(self):
        # A postponed leg's expected expiration still on the original day (Sun Nov 22 16:45 Tennis).
        data = slot()
        stale = {t: pl.Quote(q.yes_bid, q.yes_ask, q.at, COMMIT + timedelta(minutes=30)) for t, q in data.quotes.items()}
        data = ps.SlotQuotes(key=data.key, markets=data.markets, quotes=stale, cut={}, requote=data.requote,
                             started_at=data.started_at, finished_at=data.finished_at, read_seconds=1.0)
        a = agent(data)
        a._tick_run_id = 1
        (p,) = a.build_commitment(a.form_thesis(a.observe()))
        self.assertEqual(p.resolves_after, p.closes_at + timedelta(minutes=1))
        self.assertGreater(p.resolves_after, p.closes_at)


class ClvSummary(unittest.TestCase):
    """P2.1 §6: never a CLV figure without its ages; cohorts and scopes never pooled."""

    def row(self, agent="parlay_safer", clv="0.01", ages=(5.0, 9.0), version="P2.1", scope="sport"):
        return {"agent": agent, "scope": scope, "rule_version": version, "clv": clv, "clv_pct": "0.02",
                "detail": {"legs": [{"ticker": f"T{i}", "age_min": a} for i, a in enumerate(ages)]}}

    def test_the_ages_sit_beside_every_mean_per_agent_scope_and_cohort(self):
        from consensus import parlay_clv
        lines = parlay_clv.summarize([self.row(clv="0.01", ages=(5, 9)), self.row(clv="0.03", ages=(1, 27)),
                                      self.row(version="P2", ages=(2,)), self.row(scope="cross_sport", ages=(3,))])
        self.assertEqual([(x.scope, x.rule_version, x.tickets) for x in lines],
                         [("cross_sport", "P2.1", 1), ("sport", "P2", 1), ("sport", "P2.1", 2)])
        p21 = lines[2]
        self.assertEqual((p21.mean_clv, p21.median_age_min, p21.max_age_min, p21.legs), (D("0.02"), 7.0, 27, 4))
        self.assertIn("close age median 7 min, max 27 min", p21.text())

    def test_a_p2_close_without_recorded_ages_takes_them_from_its_kickoff_and_candle_end(self):
        from consensus import parlay_clv
        detail = {"legs": [{"ticker": "A", "kickoff": "2026-11-22T20:00:00+00:00",
                            "candle_end": "2026-11-22T19:57:00+00:00"}]}
        self.assertEqual(parlay_clv.leg_ages(detail), [3.0])

    def test_no_ages_no_figure(self):
        from consensus import parlay_clv
        with self.assertRaises(parlay_clv.MissingAges):
            parlay_clv.summarize([{"agent": "parlay_mid", "clv": "0.01", "detail": {"legs": [{"ticker": "A"}]}}])


# --------------------------------------------------------------------------- the image copy
class PreregCopy(unittest.TestCase):
    def test_the_image_copy_is_byte_identical_to_the_original(self):
        self.assertTrue(paths.PARLAYS_PREREG_ORIGINAL.exists())
        self.assertEqual(paths.PARLAYS_PREREG_COPY.read_bytes(), paths.PARLAYS_PREREG_ORIGINAL.read_bytes(),
                         "workers/prereg/preregistration_parlays.md differs: copy docs/preregistration_parlays.md over it")

    def test_drift_refuses_and_the_copy_alone_is_read_on_railway(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig, copy = Path(tmp) / "orig.md", Path(tmp) / "copy.md"
            orig.write_bytes(b"## 0. A\n\nx\n")
            copy.write_bytes(b"## 0. A\n\ny\n")
            with self.assertRaisesRegex(pr.PreregistrationMissing, "drifted"):
                parlay_prereg.text(orig, copy)
            copy.write_bytes(b"## 0. A\r\n\r\nx\r\n")  # line endings are not drift
            self.assertEqual(pr.normalize(parlay_prereg.text(orig, copy)), "## 0. A\n\nx")
            orig.unlink()
            self.assertIn("x", parlay_prereg.text(orig, copy))  # Railway: no docs/, the copy is read
            copy.unlink()
            with self.assertRaises(pr.PreregistrationMissing):
                parlay_prereg.text(orig, copy)


if __name__ == "__main__":
    unittest.main()
