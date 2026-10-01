"""Market-blend diagnostic on the best props variant (docs/dev/props_variants.md).

    python -m jobs.dev_props_blend --variant V2

p_blend = w·p_model + (1 − w)·p_mid. For binary settlements y:

    Brier(w) = Σ (e + w·d)²,  e = p_mid − y,  d = p_model − p_mid
             = S_ee + 2w·S_ed + w²·S_dd   →   w* = clip(−S_ed / S_dd, 0, 1)

so each game reduces to (S_ee, S_ed, S_dd) and the bootstrap over whole games
is exact and fast. Reports the walk-forward out-of-sample blend (each week's w
from earlier weeks, on the 0.05 grid) and the pooled w* with a game-clustered
95% CI. Appends a row to the props_variants.md run log.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from jobs.dev_props_variants import DRAWS, EVAL_FROM_WEEK, SEED, append_log

GRID = [round(0.05 * i, 2) for i in range(21)]


def w_star(see: float, sed: float, sdd: float) -> float:
    return 0.0 if sdd <= 0 else min(1.0, max(0.0, -sed / sdd))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True)
    v = ap.parse_args().variant
    preds = json.loads(Path(f".cache/props_pred_{v}.json").read_text(encoding="utf-8"))
    binary = [p for p in preds if p["settle"] in (0.0, 1.0)]
    mid = lambda p: (p["bid"] + p["ask"]) / 2

    # Walk-forward: each evaluation week's w from all earlier weeks, on the grid.
    weeks = sorted({p["week"] for p in binary if p["week"] >= EVAL_FROM_WEEK})
    wf_err_blend = wf_err_mid = wf_n = 0.0
    chosen = {}
    for w in weeks:
        train = [p for p in binary if p["week"] < w]
        best = min(GRID, key=lambda g: sum((g * p["p"] + (1 - g) * mid(p) - p["settle"]) ** 2 for p in train))
        chosen[w] = best
        for p in binary:
            if p["week"] == w:
                wf_err_blend += (best * p["p"] + (1 - best) * mid(p) - p["settle"]) ** 2
                wf_err_mid += (mid(p) - p["settle"]) ** 2
                wf_n += 1

    # Pooled over the evaluation set, game-clustered bootstrap.
    per_game: dict[str, list[float]] = {}
    for p in binary:
        if p["week"] < EVAL_FROM_WEEK:
            continue
        e, d = mid(p) - p["settle"], p["p"] - mid(p)
        agg = per_game.setdefault(p["game_id"], [0.0, 0.0, 0.0])
        agg[0] += e * e; agg[1] += e * d; agg[2] += d * d
    games = list(per_game.values())
    tot = [sum(g[i] for g in games) for i in range(3)]
    pooled = w_star(*tot)
    rng = random.Random(SEED)
    boots = []
    for _ in range(DRAWS):
        s = [0.0, 0.0, 0.0]
        for _ in games:
            g = rng.choice(games)
            s[0] += g[0]; s[1] += g[1]; s[2] += g[2]
        boots.append(w_star(*s))
    boots.sort()
    ci = [boots[int(0.025 * DRAWS)], boots[int(0.975 * DRAWS) - 1]]
    report = {
        "variant": v, "n_games": len(games), "n_rungs": int(wf_n),
        "walk_forward": {"weights_by_week": chosen,
                         "brier_blend": wf_err_blend / wf_n, "brier_mid": wf_err_mid / wf_n},
        "pooled_w": pooled, "pooled_w_ci95_game_clustered": ci,
        "ci_excludes_zero": ci[0] > 0,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(f"docs/dev/props-blend-{v}-{stamp}.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    append_log(f"blend({v})", "ran",
               f"pooled w {pooled:.3f} CI [{ci[0]:.3f}, {ci[1]:.3f}]; walk-forward Brier blend "
               f"{report['walk_forward']['brier_blend']:.5f} vs mid {report['walk_forward']['brier_mid']:.5f}; `{out.name}`")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
