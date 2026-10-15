"""Screening Room recommendations by hand (Chat 4 Phase 4; db/053).

    ..\\venv\\Scripts\\python.exe -m scripts.screening_recs --dry-run     (from workers/)
    ..\\venv\\Scripts\\python.exe -m scripts.screening_recs --run
    ..\\venv\\Scripts\\python.exe -m scripts.screening_recs --rerank

--dry-run  day one from the seeds alone (screening/seeds.py), live TMDB and
           the embedder, NOTHING written: the 12 picks with their "because",
           and the seen-it pass. Needs no migration.
--run      the full nightly run now (screening_recs), even if today's ran.
--rerank   the queued re-rank now: the stored pool against the record.

Prints titles and counts only; never a token.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from core import ledger
from core.paths import load_env


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--rerank", action="store_true")
    args = ap.parse_args(argv)
    load_env()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from core import embed
    from screening import rec_jobs, seeds, tmdb

    if args.dry_run:
        http, close = tmdb.httpx_get()
        try:
            t = tmdb.Tmdb(tmdb.read_token(os.environ), http)
            today = datetime.now(ZoneInfo(rec_jobs.OWNER_TZ)).date()
            r = rec_jobs.dry_run(t, embed.embed, today, notes=seeds.NOTES, seeds=seeds.SEEDS)
        finally:
            close()
        print(f"pool {r.pool}, at the floor {r.floor_kept}; {r.stats.line()}; {r.calls} TMDB calls")
        for i, (s, d, lanes) in enumerate(r.picks, 1):
            tonight = f" · tonight {', '.join(d.title.tonight)}" if d.title.tonight else ""
            print(f"{i:2}. {d.title.name} ({d.title.kind}, {d.title.vote_average}/{d.title.vote_count}) "
                  f"because you loved {r.seed_names.get(s.because)}{tonight} · {s.score:.2f} · {'/'.join(lanes)}")
        print("seen-it pass:")
        for d, grp in r.seen_pass:
            print(f"  {grp:6} {d.title.name} ({d.title.kind})")
        return 0
    try:
        run = rec_jobs.recs_job(force=True, reason="manual") if args.run else rec_jobs.rerank_job()
        print(run.job_note)
        for i, name in enumerate(run.top, 1):
            print(f"{i:2}. {name}")
        return 0
    finally:
        ledger.close_pool()


if __name__ == "__main__":
    sys.exit(main())
