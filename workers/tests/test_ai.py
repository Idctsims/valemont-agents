"""core/ai.py: cost math, the monthly budget guard, and the call wrapper.

The Anthropic SDK is never called: a stub client stands in, the five ledger
functions are an in-memory fake, push.send is stubbed, and the real pool
raises on contact.
"""

from __future__ import annotations

import os
import re
import unittest
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock

from core import ai, ledger, push
from core.ai import AiError, AiUnavailable, BudgetExceeded, Usage, cost_usd

M = 1_000_000


def _refuse_database(*a: Any, **k: Any) -> Any:
    raise AssertionError("an ai test reached the real database pool")


class CostMath(unittest.TestCase):
    def test_opus_input_and_output(self) -> None:
        self.assertEqual(cost_usd("claude-opus-5-5", Usage(M, M)), Decimal("24.000000"))

    def test_cache_rates_per_model(self) -> None:
        self.assertEqual(cost_usd("claude-opus-5-5", Usage(0, 0, cache_read=M)), Decimal("0.200000"))
        self.assertEqual(cost_usd("claude-fable-5-1", Usage(0, 0, cache_read=M)), Decimal("0.250000"))
        # 0.05x input since the 2026-10-10 verification (was wrongly 0.20).
        self.assertEqual(cost_usd("claude-sonnet-5-5", Usage(0, 0, cache_read=M)), Decimal("0.100000"))
        self.assertEqual(cost_usd("claude-opus-5-5", Usage(0, 0, cache_write_5m=M)), Decimal("5.000000"))
        self.assertEqual(cost_usd("claude-opus-5-5", Usage(0, 0, cache_write_1h=M)), Decimal("8.000000"))

    def test_haiku_switches_rate_card_above_100k_prompt_tokens(self) -> None:
        at_limit = Usage(100_000, 1000)
        over = Usage(100_001, 1000)
        self.assertEqual(cost_usd("claude-haiku-5-5", at_limit), Decimal("0.010500"))
        self.assertEqual(cost_usd("claude-haiku-5-5", over), Decimal("0.052501"))
        # Cached prompt tokens count toward the prompt length too.
        cached = Usage(10_000, 0, cache_read=95_000)
        self.assertEqual(cost_usd("claude-haiku-5-5", cached), Decimal("0.009750"))

    def test_batch_halves_every_rate(self) -> None:
        u = Usage(M, M, cache_read=M, cache_write_5m=M)
        self.assertEqual(cost_usd("claude-sonnet-5-5", u, batch=True),
                         cost_usd("claude-sonnet-5-5", u) / 2)
        self.assertEqual(cost_usd("claude-opus-5-5", Usage(M, M), batch=True), Decimal("12.000000"))

    def test_unknown_model_refuses_rather_than_guessing(self) -> None:
        with self.assertRaises(AiError):
            cost_usd("claude-opus-9", Usage(1, 1))

    def test_usage_from_response_splits_cache_writes_by_ttl(self) -> None:
        u = Usage.from_response(SimpleNamespace(
            input_tokens=10, output_tokens=20, cache_read_input_tokens=30,
            cache_creation_input_tokens=70,
            cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=40, ephemeral_1h_input_tokens=30)))
        self.assertEqual((u.tokens_in, u.tokens_out, u.cache_read, u.cache_write_5m, u.cache_write_1h),
                         (10, 20, 30, 40, 30))

    def test_usage_without_a_ttl_split_prices_writes_as_5m(self) -> None:
        u = Usage.from_response(SimpleNamespace(
            input_tokens=1, output_tokens=1, cache_read_input_tokens=None,
            cache_creation_input_tokens=50, cache_creation=None))
        self.assertEqual((u.cache_write_5m, u.cache_write_1h, u.cache_read), (50, 0, 0))


@dataclass
class FakeLedger:
    spent: Decimal = Decimal("0")
    sent: set[str] = field(default_factory=set)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def ai_spend_month_to_date(self) -> Decimal:
        return self.spent

    def notification_sent_this_month(self, kind: str) -> bool:
        return kind in self.sent

    def record_ai_usage(self, **row: Any) -> int:
        self.rows.append(row)
        return 1000 + len(self.rows)


class StubClient:
    def __init__(self, served_by: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.served_by = served_by
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        return SimpleNamespace(
            model=self.served_by or kw["model"],
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=500, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0, cache_creation=None),
        )


class Guarded(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = FakeLedger()
        self.pushes: list[push.Message] = []

        def fake_send(message: push.Message, **_: Any) -> list[Any]:
            self.ledger.sent.add(message.kind)  # what the real row would record
            self.pushes.append(message)
            return []

        patches = [
            mock.patch.object(ledger, "_pool", _refuse_database),
            mock.patch.object(push, "send", fake_send),
            mock.patch.dict(os.environ, {"AI_MONTHLY_BUDGET_USD": "10", "ANTHROPIC_API_KEY": ""}),
        ]
        for name in ("ai_spend_month_to_date", "notification_sent_this_month", "record_ai_usage"):
            patches.append(mock.patch.object(ledger, name, getattr(self.ledger, name)))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = StubClient()

    def call(self, **kw: Any) -> Any:
        return ai.complete(purpose="test_call", messages=[{"role": "user", "content": "hi"}],
                           client=self.client, **kw)

    def test_under_80_percent_runs_as_asked_and_records_the_cost(self) -> None:
        self.ledger.spent = Decimal("7.99")
        self.call()
        self.assertEqual(self.client.calls[0]["model"], "claude-opus-5-5")
        [row] = self.ledger.rows
        self.assertEqual((row["purpose"], row["model"], row["tokens_in"], row["tokens_out"]),
                         ("test_call", "claude-opus-5-5", 1000, 500))
        self.assertEqual(row["cost_usd"], Decimal("0.014000"))  # 1000*$4 + 500*$20 per M
        self.assertEqual(self.pushes, [])

    def test_on_recorded_gets_the_row_id_model_and_cost(self) -> None:
        seen: list[tuple[int, str, Decimal]] = []
        self.call(on_recorded=lambda i, m, c: seen.append((i, m, c)))
        self.assertEqual(seen, [(1001, "claude-opus-5-5", Decimal("0.014000"))])
        self.assertNotIn("on_recorded", self.client.calls[0], "never passed on to the API")

    def test_at_80_percent_non_critical_is_downgraded_and_pushed_once(self) -> None:
        self.ledger.spent = Decimal("8.00")
        self.call()
        self.call()
        self.assertEqual([c["model"] for c in self.client.calls],
                         ["claude-haiku-5-5", "claude-haiku-5-5"])
        self.assertEqual([m.kind for m in self.pushes], ["ai_budget_80"])
        self.assertEqual(self.ledger.rows[0]["model"], "claude-haiku-5-5")

    def test_at_80_percent_critical_keeps_its_model(self) -> None:
        self.ledger.spent = Decimal("9.50")
        self.call(critical=True)
        self.assertEqual(self.client.calls[0]["model"], "claude-opus-5-5")
        self.assertTrue(self.ledger.rows[0]["critical"])

    def test_at_100_percent_non_critical_is_refused_loudly_and_never_sent(self) -> None:
        self.ledger.spent = Decimal("10.00")
        with self.assertRaises(BudgetExceeded):
            self.call()
        with self.assertRaises(BudgetExceeded):
            self.call()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.ledger.rows, [])
        self.assertEqual([m.kind for m in self.pushes], ["ai_budget_100"])

    def test_at_100_percent_critical_still_runs(self) -> None:
        self.ledger.spent = Decimal("12.00")
        self.call(critical=True)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual([m.kind for m in self.pushes], ["ai_budget_100"])

    def test_a_threshold_pushed_earlier_this_month_is_not_pushed_again(self) -> None:
        self.ledger.spent = Decimal("8.50")
        self.ledger.sent = {"ai_budget_80"}
        self.call()
        self.assertEqual(self.pushes, [])

    def test_a_failed_push_does_not_block_the_call(self) -> None:
        self.ledger.spent = Decimal("8.50")
        with mock.patch.object(push, "send", side_effect=push.PushError("no devices")):
            self.call()
        self.assertEqual(len(self.client.calls), 1)

    def test_the_serving_model_is_what_gets_priced(self) -> None:
        self.client = StubClient(served_by="claude-sonnet-5-5")  # e.g. a refusal fallback
        self.call()
        self.assertEqual(self.ledger.rows[0]["model"], "claude-sonnet-5-5")
        self.assertEqual(self.ledger.rows[0]["cost_usd"], Decimal("0.007000"))

    def test_params_pass_through(self) -> None:
        self.call(system="be brief", output_config={"effort": "low"})
        self.assertEqual(self.client.calls[0]["system"], "be brief")
        self.assertEqual(self.client.calls[0]["output_config"], {"effort": "low"})

    def test_missing_api_key_refuses_loudly_before_anything_else(self) -> None:
        with self.assertRaises(AiUnavailable) as ctx:
            ai.complete(purpose="test_call", messages=[])
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))
        self.assertEqual(self.ledger.rows, [])

    def test_an_unpriced_model_is_refused_before_the_request(self) -> None:
        with self.assertRaises(AiError):
            self.call(model="claude-opus-9")
        self.assertEqual(self.client.calls, [])

    def test_budget_env(self) -> None:
        self.assertEqual(ai.monthly_budget({}), Decimal("10"))
        self.assertEqual(ai.monthly_budget({"AI_MONTHLY_BUDGET_USD": " 25.50 "}), Decimal("25.50"))
        for raw in ("0", "-5", "ten", "NaN", "inf"):
            with self.subTest(raw=raw), self.assertRaises(AiError):
                ai.monthly_budget({"AI_MONTHLY_BUDGET_USD": raw})


class WebTwinPricing(unittest.TestCase):
    """apps/web/src/lib/ai/pricing.ts must price every call exactly as core/ai.py
    does (CLAUDE.md §6, the TypeScript twin). This parses the TS literals."""

    FIELDS = (("input", "input"), ("output", "output"), ("cacheWrite5m", "cache_write_5m"),
              ("cacheWrite1h", "cache_write_1h"), ("cacheRead", "cache_read"))

    @classmethod
    def setUpClass(cls) -> None:
        from core.paths import REPO_ROOT
        cls.source = (REPO_ROOT / "apps/web/src/lib/ai/pricing.ts").read_text(encoding="utf-8")

    def rates(self, text: str) -> ai.Rates:
        values = {}
        for ts_name, py_name in self.FIELDS:
            match = re.search(rf"\b{ts_name}: ([0-9_.]+)", text)
            self.assertIsNotNone(match, f"{ts_name} missing in {text!r}")
            values[py_name] = Decimal(match.group(1).replace("_", ""))
        return ai.Rates(**values)

    def web_pricing(self) -> dict[str, ai.Price]:
        body = self.source.split("export const PRICING", 1)[1].split("\n};", 1)[0]
        prices: dict[str, ai.Price] = {}
        for model, block in re.findall(r'"(claude-[a-z0-9-]+)": \{(.*?)\n  \},', body, re.S):
            standard = re.search(r"standard: \{([^}]*)\}", block)
            long_prompt = re.search(r"longPrompt: \{([^}]*)\}", block)
            over = re.search(r"longPromptOver: ([0-9_]+)", block)
            prices[model] = ai.Price(
                standard=self.rates(standard.group(1)),
                long_prompt=self.rates(long_prompt.group(1)) if long_prompt else None,
                long_prompt_over=int(over.group(1).replace("_", "")) if over else 100_000,
            )
        return prices

    def test_every_model_and_rate_matches(self) -> None:
        web = self.web_pricing()
        self.assertEqual(sorted(web), sorted(ai.PRICING), "the same models are priced")
        for model, price in ai.PRICING.items():
            with self.subTest(model=model):
                self.assertEqual(web[model], price)

    def test_the_downgrade_model_matches(self) -> None:
        match = re.search(r'export const CHEAPEST_MODEL = "([^"]+)"', self.source)
        self.assertEqual(match.group(1), ai.CHEAPEST_MODEL)


if __name__ == "__main__":
    unittest.main()
