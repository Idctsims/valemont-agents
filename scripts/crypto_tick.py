"""Watch one crypto tick against live data. Writes nothing.

    python scripts/crypto_tick.py

Runs `observe()` and `form_thesis()` against the real Coinbase feed and prints
what the agent sees and what it would do — then stops. No run is opened, no
commitment is written, no event is emitted.

That matters here in a way it did not for the fake agent: `crypto` is
`is_test = false`, so its rows land in the real track record and cannot be
deleted. This script exists so the rule can be watched, tuned and argued with
before any of that becomes permanent.

`--commit` performs one real `run_once()` instead. It will write a permanent
commitment if the rule fires. It asks first.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from adapters.crypto import (
    HORIZON, MIN_VOL_FRACTION, UNIVERSE, Z_ENTRY, CryptoAgent, FeedError,
)
from core import ledger

load_dotenv()


def show(commit: bool) -> int:
    agent = CryptoAgent()

    print(f"universe: {', '.join(UNIVERSE)}")
    print(f"rule:     long when z <= -{Z_ENTRY}, short when z >= +{Z_ENTRY}; "
          f"vol floor {MIN_VOL_FRACTION}, horizon {HORIZON}")
    print(f"patience: {agent.defer_policy.max_attempts} attempts / "
          f"{agent.defer_policy.max_overdue}\n")

    try:
        observation = agent.observe()
    except FeedError as exc:
        print(f"FEED DOWN — every symbol defective.\n  {exc}")
        print("\nA real tick would mark the run errored. That is the intent: a "
              "dead feed must not look like a quiet market.")
        return 1

    print(f"{'symbol':<10} {'price':>12} {'sma24':>12} {'sigma':>9} "
          f"{'vol':>8} {'z':>7}  signal")
    print("-" * 72)
    for s in observation.snapshots:
        if s.z <= -Z_ENTRY:
            signal = "LONG"
        elif s.z >= Z_ENTRY:
            signal = "SHORT"
        else:
            signal = "pass"
        print(f"{s.symbol:<10} {s.price:>12.2f} {s.sma:>12.2f} {s.sigma:>9.2f} "
              f"{s.vol_fraction:>8.5f} {s.z:>7.2f}  {signal}")

    for symbol, reason in observation.broken.items():
        print(f"{symbol:<10} BROKEN  {reason}")
    for symbol, reason in observation.quiet.items():
        print(f"{symbol:<10} quiet   {reason}")

    held = agent.open_subjects()
    print(f"\ncurrently held: {', '.join(sorted(held)) or 'nothing'}")

    thesis = agent.form_thesis(observation)
    if thesis is None:
        print("\nPASSING TICK — no commitment.")
        print("This is the common case. An agent that commits every tick is "
              "ticking, not reasoning.")
        return 0

    print(f"\nCOMMITTING TICK — {thesis.direction.upper()}")
    print(f"  subject      {thesis.symbol}")
    print(f"  entry        {thesis.entry}")
    print(f"  size         {thesis.size:.6f}  (fixed notional)")
    print(f"  target       {thesis.target:.2f}")
    print(f"  invalidation {thesis.stop:.2f}   <- the pnl denominator (§9)")
    print(f"  risk/unit    {abs(thesis.entry - thesis.stop):.2f}  "
          f"({abs(thesis.entry - thesis.stop) / thesis.entry:.4f} of entry)")
    print(f"  confidence   {thesis.confidence}")
    print(f"  resolves in  {HORIZON}")
    print(f"\n  {thesis.rationale}")

    if not commit:
        print("\n(nothing written — pass --commit to make this real)")
        return 0

    print("\n--commit given. This writes a PERMANENT row to the real track "
          "record.")
    if input("Type 'commit' to proceed: ").strip() != "commit":
        print("aborted, nothing written")
        return 0

    outcome = agent.run_once()
    print(f"run {outcome.run_id}: status={outcome.status} "
          f"commitment={outcome.commitment_id}")
    return 0 if outcome.status == "ok" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit", action="store_true",
        help="actually run the cycle and write a real commitment if it fires",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)-7s %(name)-20s %(message)s")
    try:
        return show(args.commit)
    finally:
        ledger.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
