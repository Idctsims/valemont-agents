"""Run one Arsenal pool batch now, wait for it, collect it, verify inline.

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_pool --now      (from workers/)

The same code as the worker's arsenal_pool and arsenal_pool_collect jobs
(arsenal/pool.py, arsenal/collect.py). If a batch is already open (the
worker's boot run submitted one), it waits on that one instead of
submitting a second. Then every pending real item (this batch's and the
seeds') is verified inline by arsenal/verify.py. Prints counts and cost
only.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

from arsenal import collect, pool, verify
from core import ai, ledger
from core.paths import load_env

POLL_S = 30


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--now", action="store_true", required=True, help="submit (or join) a batch now")
    parser.add_argument("--wait-min", type=int, default=120, help="give up waiting after this many minutes")
    args = parser.parse_args(argv)

    load_env()
    started = datetime.now(timezone.utc)
    try:
        print(f"embedded: {pool.embed_missing()}")
        open_batch = ledger.arsenal_open_batch()
        if open_batch is None:
            run_id, batch_id = pool.submit()
            print(f"submitted: batch {batch_id} (run {run_id})")
            open_batch = ledger.arsenal_open_batch()
        else:
            print(f"joined: batch {open_batch.batch_id} (run {open_batch.run_id}), already open")
        assert open_batch is not None
        deadline = time.monotonic() + args.wait_min * 60
        while (status := ai.batch_status(open_batch.batch_id)) != "ended":
            if time.monotonic() > deadline:
                print(f"still {status} after {args.wait_min} min: the worker's arsenal_pool_collect will collect it")
                return 2
            time.sleep(POLL_S)
        run = collect.collect(open_batch, queue=False)
        print(run.job_note)
        if run.inserted is not None:
            # Every candidate's nearest real reference (owner, 2026-10-16):
            # the skips, then the distribution, before 0.92 is tuned.
            for n in run.inserted.near_dup_skips:
                print(f"near-dup skip: {n.key} ({n.title}) ~ {n.matched}, cosine {n.cosine:.4f}")
            scores = sorted(n.cosine for n in run.inserted.nearest if n.cosine is not None)
            if scores:
                print("nearest cosine: " + ", ".join(f"{s:.3f}" for s in scores))
                edges = (0.5, 0.7, 0.8, 0.85, 0.9, collect.NEAR_DUP, 1.01)
                lo = 0.0
                for hi in edges:
                    print(f"  [{lo:.2f}, {min(hi, 1.0):.2f}{']' if hi > 1 else ')'}: {sum(lo <= s < hi for s in scores)}")
                    lo = hi
        outcomes: dict[str, int] = {}
        for target, item_id in ledger.arsenal_stale_pending(0):
            result = verify.verify_item(target, item_id)
            outcomes[f"{target} {result}"] = outcomes.get(f"{target} {result}", 0) + 1
        print("verified: " + (", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())) or "nothing pending"))
        ledger.arsenal_close_run_if_settled(open_batch.run_id)
        for row in ledger.arsenal_run_rows(open_batch.run_id):
            counts = {k: v for k, v in row.items() if k not in ("id", "status", "batch_id", "model", "note") and v}
            print(f"run row {row['id']} {row['status']}: {counts}" + (f" | {row['note']}" if row["note"] else ""))
        costs = ledger.arsenal_ai_cost_since(started)
        print("cost: " + (", ".join(f"{p} ${c}" for p, c in costs.items()) or "$0")
              + f" (total ${sum(costs.values())})")
    finally:
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
