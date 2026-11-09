"""Run the consensus job by hand (db/073; consensus/job.py). From workers/:

    ..\\venv\\Scripts\\python.exe -m scripts.consensus --dry-run [--replay DIR]
    ..\\venv\\Scripts\\python.exe -m scripts.consensus            (writes; spends credits)

--dry-run   reads the board and ESPN, writes nothing, records no call, and
            refuses every PAID Odds API call (free /events still runs).
--replay    serves saved Odds API bodies instead of the network: DIR holds
            odds_nfl.json / odds_nba.json (bulk), events_nfl.json /
            events_nba.json, props_<eventid>.json. No key is needed; nothing
            is spent.

Prints the run note. Never prints the key.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from core.paths import ENV_FILE


class DryStore:
    """Real reads; every write counted, none sent."""

    def __init__(self) -> None:
        from consensus.job import Store

        self._real = Store()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def record_call(self, row: Mapping[str, Any]) -> None:
        return None

    def upsert(self, rows, at) -> int:
        self.rows = list(rows)
        return len(rows)

    def sweep(self, games, keep) -> int:
        return 0

    def lines(self, rows) -> int:
        return len(rows)

    def prune(self) -> int:
        return 0


def replay_http(folder: Path):
    from venues.odds.client import Resp

    def get(url: str, params: Mapping[str, str]) -> Resp:
        path = url.split("/v4", 1)[1]
        parts = path.strip("/").split("/")
        sport = parts[1].split("_")[-1] if len(parts) > 1 else ""
        if path == "/sports":
            name = "sports.json"
        elif parts[-1] == "events":
            name = f"events_{sport}.json"
        elif len(parts) == 3:
            name = f"odds_{sport}.json"
        else:
            name = f"props_{parts[3]}.json"
        f = folder / name
        if not f.exists():
            return Resp(404, None, {}, 0)
        body = f.read_bytes()
        return Resp(200, json.loads(body), {"x-requests-last": "0"}, len(body))

    return get


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--replay", type=Path)
    a = ap.parse_args(argv)
    load_dotenv(ENV_FILE, override=False)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from consensus import job
    from core.odds_credits import CreditRefused

    store = DryStore() if a.dry_run else None
    kw: dict[str, Any] = {}
    if a.replay:
        kw = {"odds_http": replay_http(a.replay), "key": "replay"}
    if a.dry_run and not a.replay:
        # Free calls only: the guard refuses every paid call.
        import core.odds_credits as oc

        def refuse(self, cost: int) -> None:
            self.refused.append("dry run")
            raise CreditRefused("dry run: paid calls refused")
        oc.CreditGuard.check = refuse  # type: ignore[method-assign]
        oc.after_run = lambda guard: False  # type: ignore[assignment]
    try:
        run = job.consensus_job(store=store, state=job.DayState(), **kw)
    except job.ConsensusFailed as exc:
        print("FAILED:", exc)
        return 1
    print(run.job_note)
    if a.dry_run and store is not None and getattr(store, "rows", None):
        for r in store.rows[:15]:
            print(" ", r["market_ticker"], r["tier"], r["n_books"], r.get("p_books") or r.get("p_espn"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
