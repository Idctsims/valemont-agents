"""The ONLY way worker code calls Claude.

Every call goes through `complete()`, which:

  1. refuses loudly if ANTHROPIC_API_KEY is missing (AiUnavailable). This is
     an exception the caller's job records, never a crash of the worker;
  2. applies the monthly budget (AI_MONTHLY_BUDGET_USD, default $10; the
     owner decided $20 on 2026-10-09; summed
     from ai_usage since the 1st of the owner's month):
       >= 80%   non-critical calls are downgraded to the cheapest model;
                one push that month
       >= 100%  non-critical calls are refused (BudgetExceeded); critical
                calls still run; one push that month
  3. makes the request with the official SDK;
  4. writes one ai_usage row with the cost computed from the response's
     usage and the pricing table below.

`record_usage()` is the same accounting for work that does not go through
`complete()` (Message Batches results, from Chat 3). `cost_usd()` is pure and
public so the web twin's pricing file (Chat 2) can be tested against it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Mapping

from core import ledger, push

log = logging.getLogger("valemont.ai")

# ---------------------------------------------------------------------------
# Pricing, USD per million tokens. Anthropic first-party API rates, VERIFIED
# 2026-10-10 against https://platform.claude.com/docs/en/about-claude/pricing
# (docs.claude.com redirects there) and .../models/overview for the IDs.
# The 2026-10-06 entry had Sonnet 5.5 cache reads at $0.20; the page says
# $0.10 (0.05x input, footnote 2). Every other rate below was unchanged.
# Update together with apps/web/src/lib/ai/pricing.ts; tests/test_ai.py
# (WebTwinPricing) fails if the two disagree.
#
#   cache writes: 1.25x input (5-minute TTL), 2x input (1-hour TTL)
#   cache reads:  0.1x input, except Opus 5.5 and Sonnet 5.5 (0.05x) and
#                 Fable 5.1 (0.025x)
#   batch:        50% of every rate (multipliers stack)
#   Haiku 5.5:    two rate cards by prompt length: <= 100K tokens, and longer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rates:
    input: Decimal
    output: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal
    cache_read: Decimal


@dataclass(frozen=True)
class Price:
    standard: Rates
    #: Applies when the prompt (input + cache reads + cache writes) is longer
    #: than `long_prompt_over` tokens. None: one rate card at any length.
    long_prompt: Rates | None = None
    long_prompt_over: int = 100_000


def _rates(i: str, o: str, w5: str, w1: str, r: str) -> Rates:
    return Rates(*(Decimal(x) for x in (i, o, w5, w1, r)))


PRICING: Mapping[str, Price] = {
    "claude-fable-5-1": Price(_rates("10", "50", "12.50", "20", "0.25")),
    "claude-opus-5-5": Price(_rates("4", "20", "5", "8", "0.20")),
    "claude-sonnet-5-5": Price(_rates("2", "10", "2.50", "4", "0.10")),
    "claude-haiku-5-5": Price(
        _rates("0.10", "0.50", "0.125", "0.20", "0.01"),
        long_prompt=_rates("0.50", "2.50", "0.625", "1.00", "0.05"),
    ),
}

BATCH_DISCOUNT = Decimal("0.5")
DEFAULT_MODEL = "claude-opus-5-5"
#: Where non-critical calls go once 80% of the month's budget is spent.
CHEAPEST_MODEL = "claude-haiku-5-5"

DEFAULT_BUDGET_USD = Decimal("10")
DOWNGRADE_AT = Decimal("0.8")


class AiError(RuntimeError):
    """A Claude call could not be made, or could not be accounted for."""


class AiUnavailable(AiError):
    """ANTHROPIC_API_KEY is not set."""


class BudgetExceeded(AiError):
    """The month's budget is spent and the call was not critical."""


# ------------------------------------------------------------------ usage

@dataclass(frozen=True)
class Usage:
    tokens_in: int
    tokens_out: int
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h

    @property
    def prompt(self) -> int:
        return self.tokens_in + self.cache_read + self.cache_write

    @classmethod
    def from_response(cls, usage: Any) -> "Usage":
        """From an SDK `Message.usage`. `cache_creation` splits writes by TTL;
        when absent, every write is priced as the default 5-minute TTL."""
        written = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        split = getattr(usage, "cache_creation", None)
        w1h = int(getattr(split, "ephemeral_1h_input_tokens", 0) or 0) if split else 0
        w5m = int(getattr(split, "ephemeral_5m_input_tokens", 0) or 0) if split else written
        return cls(
            tokens_in=int(usage.input_tokens or 0),
            tokens_out=int(usage.output_tokens or 0),
            cache_read=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_write_5m=w5m,
            cache_write_1h=w1h,
        )


_MILLION = Decimal(1_000_000)
_MICRO = Decimal("0.000001")


def cost_usd(model: str, usage: Usage, *, batch: bool = False) -> Decimal:
    """Exact cost of one call, rounded to the micro-dollar (ai_usage scale)."""
    price = PRICING.get(model)
    if price is None:
        # Never guess: a cost recorded at the wrong rate corrupts the budget.
        raise AiError(f"no pricing for model {model!r}; add it to core/ai.py PRICING")
    rates = (price.long_prompt
             if price.long_prompt is not None and usage.prompt > price.long_prompt_over
             else price.standard)
    total = (usage.tokens_in * rates.input
             + usage.tokens_out * rates.output
             + usage.cache_write_5m * rates.cache_write_5m
             + usage.cache_write_1h * rates.cache_write_1h
             + usage.cache_read * rates.cache_read) / _MILLION
    if batch:
        total *= BATCH_DISCOUNT
    return total.quantize(_MICRO, rounding=ROUND_HALF_UP)


# ----------------------------------------------------------------- budget

def monthly_budget(env: Mapping[str, str] = os.environ) -> Decimal:
    raw = env.get("AI_MONTHLY_BUDGET_USD", "").strip()
    if not raw:
        return DEFAULT_BUDGET_USD
    try:
        value = Decimal(raw)
    except Exception:
        value = Decimal(-1)
    if not value.is_finite() or value <= 0:
        raise AiError(f"AI_MONTHLY_BUDGET_USD must be a positive number of dollars, got {raw!r}")
    return value


def _notify_once(kind: str, title: str, body: str) -> None:
    """One push per kind per month. A failed push never blocks the call."""
    try:
        if ledger.notification_sent_this_month(kind):
            return
        push.send(push.Message(kind=kind, title=title, body=body,
                               deep_link="/settings/health", tag="ai-budget"))
    except Exception as exc:
        log.error("budget alert %s not sent: %s: %s", kind, type(exc).__name__, exc)


def guard(model: str, *, critical: bool) -> str:
    """Apply the monthly budget. Returns the model to use, or raises
    BudgetExceeded for a non-critical call once the budget is spent."""
    budget = monthly_budget()
    spent = ledger.ai_spend_month_to_date()
    if spent >= budget:
        _notify_once("ai_budget_100", "AI budget spent",
                     f"${spent:.2f} of ${budget:.2f} this month. Non-critical AI calls "
                     "are refused until the 1st; critical ones still run.")
        if not critical:
            raise BudgetExceeded(
                f"AI budget spent (${spent:.2f} of ${budget:.2f} this month); "
                "refusing a non-critical call. Raise AI_MONTHLY_BUDGET_USD or mark it critical.")
        return model
    if spent >= budget * DOWNGRADE_AT:
        _notify_once("ai_budget_80", "AI budget at 80%",
                     f"${spent:.2f} of ${budget:.2f} this month. Non-critical AI calls "
                     f"now use {CHEAPEST_MODEL}.")
        if not critical and model != CHEAPEST_MODEL:
            log.warning("budget at %s of %s: %s downgraded to %s", spent, budget, model, CHEAPEST_MODEL)
            return CHEAPEST_MODEL
    return model


# ------------------------------------------------------------------ calls

#: Called after a call's ai_usage row is written: (row id, model, cost).
OnRecorded = Callable[[int, str, Decimal], None]


def record_usage(*, purpose: str, model: str, usage: Usage, critical: bool,
                 batch: bool = False, on_recorded: OnRecorded | None = None) -> Decimal:
    """Write the ai_usage row for one call; returns its cost."""
    cost = cost_usd(model, usage, batch=batch)
    usage_id = ledger.record_ai_usage(
        purpose=purpose, model=model, tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
        cache_read_tokens=usage.cache_read, cache_write_tokens=usage.cache_write,
        batch=batch, cost_usd=cost, critical=critical,
    )
    if on_recorded is not None:
        on_recorded(usage_id, model, cost)
    return cost


def _client() -> Any:
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        raise AiUnavailable(
            "ANTHROPIC_API_KEY is not set, so no Claude call can be made. Add it to the root "
            ".env locally, or to the Railway service variables. Refusing this call; the worker "
            "keeps running.")
    import anthropic  # deferred: importing the SDK is not free, and tests inject a client

    return anthropic.Anthropic()


def complete(*, purpose: str, messages: list[Any], model: str = DEFAULT_MODEL,
             critical: bool = False, max_tokens: int = 16_000, client: Any = None,
             on_recorded: OnRecorded | None = None, **params: Any) -> Any:
    """`client.messages.create(...)` under the budget, with its usage recorded.

    `purpose` is snake_case (ai_usage CHECK): what the call is for, e.g.
    'morning_brief'. `critical` calls run even when the budget is spent and
    are never downgraded; use it for what the owner must not lose.
    `on_recorded(usage_id, model, cost)` runs once the ai_usage row exists.
    Extra keyword arguments pass straight to messages.create (system,
    output_config, thinking, tools...).
    """
    if model not in PRICING:
        raise AiError(f"no pricing for model {model!r}; add it to core/ai.py PRICING first")
    sdk = client if client is not None else _client()
    chosen = guard(model, critical=critical)
    response = sdk.messages.create(model=chosen, max_tokens=max_tokens,
                                   messages=messages, **params)
    # The response names the model that actually served it; price that one.
    served = getattr(response, "model", None) or chosen
    if served not in PRICING:
        log.error("response served by unpriced model %r; costing it as %r", served, chosen)
        served = chosen
    cost = record_usage(purpose=purpose, model=served, usage=Usage.from_response(response.usage),
                        critical=critical, on_recorded=on_recorded)
    log.info("ai %s: %s, $%s", purpose, served, cost)
    return response
