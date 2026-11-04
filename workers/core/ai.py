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

THE ACCOUNT'S CREDIT (owner, 2026-11-03). When the Anthropic account's
prepaid balance runs out, the API answers every call with HTTP 400 "Your
credit balance is too low". That is not this project's budget (above) and
not a bug in a job: it is one outage with one cause. Every SDK call here
turns it into CreditExhausted, and:
  - ONE push per outage, "Anthropic credit balance empty — top up"
    (notification kind ai_credit_empty);
  - ONE "Anthropic credit restored" push (ai_credit_restored) on the first
    successful call after it;
  - core/jobs.run_tracked records the affected job's failure as CREDIT_NOTE,
    not the SDK's error text, and the health monitor sends no per-job
    "needs attention" for it (the one credit push covers them all);
  - the job_queue defers a queued job for an hour without spending one of
    its attempts, so an outage never exhausts one.
The outage's state is the latest of those two notifications, read once per
process by the health monitor's first pass (credit_sync), so a restart in the
middle of an outage neither repeats the push nor loses the "restored" one.
"""

from __future__ import annotations

import logging
import os
import threading
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


class CreditExhausted(AiError):
    """The Anthropic account's prepaid credit is spent (HTTP 400 "credit
    balance is too low"). Raised from the SDK's error, which stays as its
    __cause__."""


# ----------------------------------------------------------------- credit

CREDIT_EMPTY_KIND = "ai_credit_empty"
CREDIT_RESTORED_KIND = "ai_credit_restored"
#: What an affected job's job_health.last_error says. The health monitor
#: matches its prefix (credit_note) to stay quiet for that job.
CREDIT_NOTE = "credit: Anthropic credit balance empty; top up (Plans & Billing)"
_CREDIT_TEXT = "credit balance is too low"


def is_credit_error(exc: BaseException) -> bool:
    """The API's 'credit balance is too low' 400 (anthropic.BadRequestError),
    matched by status and text so no SDK import is needed."""
    return getattr(exc, "status_code", None) == 400 and _CREDIT_TEXT in str(exc).lower()


def credit_cause(exc: BaseException | None) -> CreditExhausted | None:
    """`exc` or anything in its cause/context chain that is CreditExhausted
    (a job may wrap it), else None."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, CreditExhausted):
            return exc
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return None


def credit_note(text: str | None) -> bool:
    """Does a job_health note say the job failed for the account's credit?"""
    return bool(text) and str(text).startswith(CREDIT_NOTE.split(";")[0])


_credit_lock = threading.Lock()
#: Is a credit outage open (its push delivered, no "restored" since)? None:
#: not yet known in this process; credit_sync or the first credit error reads it.
_credit_empty: bool | None = None
#: A call has succeeded in this process (for a restart mid-outage).
_credit_ok_seen = False


def _credit_push(kind: str, title: str, body: str) -> bool:
    """True only if at least one device took it; a failed push is logged."""
    try:
        results = push.send(push.Message(kind=kind, title=title, body=body,
                                         deep_link="/settings/health", tag="ai-credit"))
    except Exception as exc:
        log.error("credit alert %s not sent: %s: %s", kind, type(exc).__name__, exc)
        return False
    return any(getattr(r, "status", None) in ("sent", "partial") for r in results)


def _push_restored() -> None:
    """Under _credit_lock, with an outage open: tell the owner it is over."""
    global _credit_empty
    if _credit_push(CREDIT_RESTORED_KIND, "Anthropic credit restored",
                    "Claude calls are working again. Summaries and why-lines catch up on the next runs."):
        _credit_empty = False
        log.info("ai credit restored")


def _on_credit_error() -> None:
    """One push per outage. Never raises: the caller raises CreditExhausted."""
    global _credit_empty
    with _credit_lock:
        if _credit_empty is None:
            try:
                _credit_empty = ledger.ai_credit_outage_open()
            except Exception as exc:
                log.error("credit outage state unreadable (%s: %s); alerting", type(exc).__name__, exc)
                _credit_empty = False
        if _credit_empty:
            return
        log.error("ai: the Anthropic credit balance is empty; every Claude call fails until a top-up")
        if _credit_push(CREDIT_EMPTY_KIND, "Anthropic credit balance empty — top up",
                        "Every Claude call is refused (HTTP 400). Summaries, why-lines and the Arsenal wait; "
                        "the brief falls back. Top up at console.anthropic.com, Plans & Billing."):
            _credit_empty = True


def _on_success() -> None:
    """A call went through. Reads nothing: acts only on an outage known open."""
    global _credit_ok_seen
    _credit_ok_seen = True
    if _credit_empty:
        with _credit_lock:
            if _credit_empty:
                _push_restored()


def credit_sync() -> None:
    """Learn, once per process, whether an outage is open (the latest
    ai_credit_empty / ai_credit_restored push). The health monitor calls it
    every pass; it reads the database only the first time. A call that already
    succeeded in this process closes an outage found open."""
    global _credit_empty
    if _credit_empty is not None:
        return
    with _credit_lock:
        if _credit_empty is None:
            _credit_empty = ledger.ai_credit_outage_open()
        if _credit_empty and _credit_ok_seen:
            _push_restored()


def _sdk(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """One SDK request: the account's credit error becomes CreditExhausted."""
    try:
        out = fn(*args, **kwargs)
    except Exception as exc:
        if is_credit_error(exc):
            _on_credit_error()
            raise CreditExhausted(CREDIT_NOTE) from exc
        raise
    _on_success()
    return out


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


#: The SDK client, made once and reused: a new anthropic.Anthropic() per call
#: built a new httpx pool and TLS context every time and never closed them
#: (Chat 3 Phase 1 hotfix). The SDK client is safe to share across threads.
_SDK: Any = None


def _client() -> Any:
    global _SDK
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        raise AiUnavailable(
            "ANTHROPIC_API_KEY is not set, so no Claude call can be made. Add it to the root "
            ".env locally, or to the Railway service variables. Refusing this call; the worker "
            "keeps running.")
    if _SDK is None:
        import anthropic  # deferred: importing the SDK is not free, and tests inject a client

        _SDK = anthropic.Anthropic()
    return _SDK


def count_tokens(*, model: str, messages: list[Any], client: Any = None, **params: Any) -> int:
    """The input tokens a prompt would cost (the API's free token count; no
    model call, no ai_usage row). For measuring prompts, e.g. the Arsenal's
    never-repeat cap (owner, 2026-11-04): tokens before and after."""
    sdk = client if client is not None else _client()
    return int(_sdk(sdk.messages.count_tokens, model=model, messages=messages, **params).input_tokens)


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
    response = _sdk(sdk.messages.create, model=chosen, max_tokens=max_tokens,
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


# ---------------------------------------------------------------- batches
#
# Message Batches (Chat 5, the Arsenal's weekly pool): half price, results
# within 24 h. The budget applies at submit (the same guard as complete());
# usage is recorded per result when it is read, at the batch rate.

@dataclass(frozen=True)
class BatchRequest:
    custom_id: str
    #: messages.create parameters except `model` and `max_tokens`.
    params: Mapping[str, Any]
    max_tokens: int = 16_000


@dataclass(frozen=True)
class BatchResult:
    custom_id: str
    #: The Message when the request succeeded, else None.
    message: Any
    #: 'succeeded' | 'errored' | 'canceled' | 'expired'
    kind: str
    error: str | None = None


def submit_batch(*, requests: list[BatchRequest], model: str, critical: bool = False,
                 client: Any = None) -> tuple[str, str]:
    """Submit one Message Batch under the budget. Returns (batch id, the model
    every request uses: downgraded at 80%, refused at 100% unless critical)."""
    if model not in PRICING:
        raise AiError(f"no pricing for model {model!r}; add it to core/ai.py PRICING first")
    if not requests:
        raise AiError("a batch needs at least one request")
    sdk = client if client is not None else _client()
    chosen = guard(model, critical=critical)
    batch = _sdk(sdk.messages.batches.create, requests=[
        {"custom_id": r.custom_id, "params": {"model": chosen, "max_tokens": r.max_tokens, **r.params}}
        for r in requests
    ])
    log.info("ai batch %s submitted: %d request(s), %s", batch.id, len(requests), chosen)
    return str(batch.id), chosen


def batch_status(batch_id: str, *, client: Any = None) -> str:
    """'in_progress' | 'canceling' | 'ended'."""
    sdk = client if client is not None else _client()
    return str(_sdk(sdk.messages.batches.retrieve, batch_id).processing_status)


def batch_results(batch_id: str, *, purpose: str, model: str, critical: bool = False, client: Any = None,
                  on_recorded: OnRecorded | None = None) -> list[BatchResult]:
    """Every result of an ENDED batch. Each succeeded one writes its ai_usage
    row at the batch rate, priced by the model that served it (else `model`,
    the one submitted)."""
    sdk = client if client is not None else _client()
    out: list[BatchResult] = []
    # Listed inside _sdk: the SDK may fetch the results lazily, on iteration.
    for entry in _sdk(lambda: list(sdk.messages.batches.results(batch_id))):
        result = entry.result
        kind = str(getattr(result, "type", "errored"))
        if kind != "succeeded":
            error = getattr(result, "error", None)
            detail = getattr(getattr(error, "error", None), "message", None) or getattr(error, "type", None)
            out.append(BatchResult(entry.custom_id, None, kind, str(detail) if detail else kind))
            continue
        message = result.message
        served = getattr(message, "model", None)
        if served not in PRICING:
            log.error("batch result served by unpriced model %r; costing it as %r", served, model)
            served = model
        cost = record_usage(purpose=purpose, model=served, usage=Usage.from_response(message.usage),
                            critical=critical, batch=True, on_recorded=on_recorded)
        log.info("ai batch %s %s: %s, $%s", batch_id, entry.custom_id, served, cost)
        out.append(BatchResult(entry.custom_id, message, kind))
    return out
