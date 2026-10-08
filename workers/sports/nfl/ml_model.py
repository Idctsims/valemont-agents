"""The `nfl_ml` model: the market's price plus named, fitted adjustments.

Pre-registered in docs/preregistration_nfl.md §3.1:

    p_model(home YES) = mid_home(t) + Σ β_i · x_i          (no intercept)

The stated assumption is that the closing mid is a better probability than the
mid at t, so the model predicts the move to the close and adds it. Each term
`β_i · x_i` is written as a `commitment_factors` row (§10.1) so the ledger can
later say whether that factor ever earned its keep.

**One feature entry point, `features_asof`.** Its first act is to drop every
candle that ends after t. Nothing else in this module reads candles, so the
close — or anything after the commit instant — cannot reach a feature, and the
perturbation test (randomize everything after t, features must not move)
checks exactly that (§4).

Fitting is ridge regression with **per-factor complete cases**: a row missing
factor i contributes nothing to the equations involving i (pairwise deletion in
the normal equations), which is the pre-registered reading of "rows with a NULL
x are dropped from that coefficient's fit". λ is chosen by **walk-forward,
time-ordered validation** on development data (amendment A2): every validated
week is predicted by a fit on strictly earlier weeks only, never on a later
one. No random folds, no leave-one-out across time. The chosen λ is then fixed
by amendment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final, Iterable, Mapping, Sequence

from venues.kalshi.client import Candle

__all__ = [
    "FACTORS",
    "Features",
    "FactorModel",
    "features_asof",
    "mid_at",
    "fit_ridge",
    "choose_lambda",
    "LAMBDA_GRID",
    "walk_forward_predictions",
]

#: Names are the `commitment_factors.name` values (snake_case by CHECK).
FACTORS: Final = ("line_movement", "line_movement_late", "rest", "injury")

ANCHOR_LEAD: Final = timedelta(days=6)
LATE_WINDOW: Final = timedelta(hours=24)
STALE_AFTER: Final = timedelta(minutes=30)
MIN_ANCHOR_VOLUME: Final = Decimal(100)
REST_CLIP: Final = 7

#: The walk-forward search space for λ. Fixed here so the choice is
#: reproducible; the chosen value is recorded in the pre-registration.
LAMBDA_GRID: Final = (0.01, 0.1, 1.0, 10.0, 100.0)

#: Validation starts once this many distinct weeks are available to train on.
MIN_TRAIN_WEEKS: Final = 4


def mid_at(
    candles: Sequence[Candle], when: datetime, max_age: timedelta | None
) -> Decimal | None:
    """Mid of the latest quoted candle ending at or before `when`.

    `max_age=None` accepts any age (the 6-day anchor); otherwise a candle older
    than `max_age` is stale and returns None.
    """
    quoted = [c for c in candles if c.end <= when and c.mid is not None]
    if not quoted:
        return None
    last = max(quoted, key=lambda c: c.end)
    if max_age is not None and when - last.end > max_age:
        return None
    return last.mid


@dataclass(frozen=True, slots=True)
class Features:
    """The four factors for one game at t. None = NULL: no row, no effect."""

    mid_t: Decimal | None
    values: Mapping[str, Decimal | None]

    def present(self) -> dict[str, Decimal]:
        return {k: v for k, v in self.values.items() if v is not None}


def features_asof(
    *,
    t: datetime,
    kickoff: datetime,
    hourly: Sequence[Candle],
    minute: Sequence[Candle],
    home_rest: int | None,
    away_rest: int | None,
    qb_out_home: int | None,
    qb_out_away: int | None,
) -> Features:
    """Every `nfl_ml` feature for one game, from data available at `t` only.

    `hourly` and `minute` are the HOME team's YES market. Rest and QB status
    are known before t by construction (schedule; the week's final report,
    amendment A1), so they are passed in already resolved.
    """
    hourly = [c for c in hourly if c.end <= t]
    minute = [c for c in minute if c.end <= t]

    mid_t = mid_at(minute, t, STALE_AFTER)

    anchor = kickoff - ANCHOR_LEAD
    anchor_mid = mid_at(hourly, anchor, None)
    anchor_volume = sum((c.volume for c in hourly if anchor < c.end <= t), Decimal(0))
    if mid_t is None or anchor_mid is None or anchor_volume < MIN_ANCHOR_VOLUME:
        line_movement = None
    else:
        line_movement = mid_t - anchor_mid

    late_mid = mid_at(minute, t - LATE_WINDOW, STALE_AFTER)
    line_movement_late = None if mid_t is None or late_mid is None else mid_t - late_mid

    if home_rest is None or away_rest is None:
        rest = None
    else:
        rest = Decimal(max(-REST_CLIP, min(REST_CLIP, home_rest - away_rest)))

    if qb_out_home is None or qb_out_away is None:
        injury = None
    else:
        injury = Decimal(qb_out_away - qb_out_home)

    return Features(mid_t=mid_t, values={
        "line_movement": line_movement,
        "line_movement_late": line_movement_late,
        "rest": rest,
        "injury": injury,
    })


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FactorModel:
    """Fitted coefficients, as stored in `model_versions.params`."""

    betas: Mapping[str, Decimal]
    lam: float

    def contributions(self, features: Features) -> dict[str, Decimal]:
        """β_i · x_i for each present factor, in probability points."""
        return {
            name: (self.betas.get(name, Decimal(0)) * x).quantize(Decimal("0.000001"))
            for name, x in features.present().items()
        }

    def to_params(self) -> dict[str, object]:
        return {"betas": {k: str(v) for k, v in self.betas.items()}, "lambda": self.lam,
                "factors": list(FACTORS), "intercept": False}

    @classmethod
    def from_params(cls, params: Mapping[str, object]) -> "FactorModel":
        raw = params.get("betas")
        if not isinstance(raw, Mapping):
            raise ValueError(f"model params without betas: {params!r}")
        return cls(betas={k: Decimal(str(v)) for k, v in raw.items()},
                   lam=float(params.get("lambda", 0.0)))  # type: ignore[arg-type]


Row = tuple[Mapping[str, Decimal | None], float]


def fit_ridge(rows: Sequence[Row], lam: float) -> FactorModel:
    """Ridge, no intercept, per-factor complete cases (pairwise deletion)."""
    k = len(FACTORS)
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for x, y in rows:
        vals = [None if x.get(f) is None else float(x[f]) for f in FACTORS]  # type: ignore[arg-type]
        for i in range(k):
            if vals[i] is None:
                continue
            xty[i] += vals[i] * y
            for j in range(k):
                if vals[j] is not None:
                    xtx[i][j] += vals[i] * vals[j]
    for i in range(k):
        xtx[i][i] += lam
    betas = _solve(xtx, xty)
    return FactorModel(
        betas={f: Decimal(repr(b)) for f, b in zip(FACTORS, betas)}, lam=lam,
    )


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting. k = 4; no dependency needed."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-15:
            raise ValueError("singular system; raise lambda")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                factor = m[r][col] / m[col][col]
                m[r] = [rv - factor * cv for rv, cv in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def walk_forward_predictions(
    rows: Sequence[tuple[int, Mapping[str, Decimal | None], float]],
    lam: float,
    *,
    min_train_weeks: int = MIN_TRAIN_WEEKS,
) -> list[tuple[int, float]]:
    """Out-of-sample predictions: (row index, predicted y) for every row in a
    week after the first `min_train_weeks`, each from a fit on earlier weeks."""
    weeks = sorted({w for w, _, _ in rows})
    out: list[tuple[int, float]] = []
    for w in weeks[min_train_weeks:]:
        model = fit_ridge([(x, y) for wk, x, y in rows if wk < w], lam)
        for i, (wk, x, _) in enumerate(rows):
            if wk == w:
                out.append((i, sum(float(model.betas[f]) * float(v)
                                   for f, v in x.items() if v is not None)))
    return out


def choose_lambda(
    rows: Sequence[tuple[int, Mapping[str, Decimal | None], float]],
    grid: Iterable[float] = LAMBDA_GRID,
    *,
    min_train_weeks: int = MIN_TRAIN_WEEKS,
    fit=None,
) -> tuple[float, dict[float, float]]:
    """Walk-forward λ selection. Rows are (week_key, x, y); week_key orders
    time (season * 100 + week).

    For each week w after the first `min_train_weeks`, fit on every row with
    week_key < w and score squared error on week w. A λ's score is the mean
    over all validated rows. Nothing from week w or later ever reaches the fit
    that predicts week w. `fit` is injectable so a test can prove it.
    """
    fit = fit or fit_ridge
    weeks = sorted({w for w, _, _ in rows})
    if len(weeks) <= min_train_weeks:
        raise ValueError(
            f"walk-forward needs more than {min_train_weeks} weeks, got {len(weeks)}"
        )
    scores: dict[float, float] = {}
    for lam in grid:
        err, n = 0.0, 0
        for w in weeks[min_train_weeks:]:
            model = fit([(x, y) for wk, x, y in rows if wk < w], lam)
            for wk, x, y in rows:
                if wk != w:
                    continue
                pred = sum(float(model.betas[f]) * float(v)
                           for f, v in x.items() if v is not None)
                err += (y - pred) ** 2
                n += 1
        scores[lam] = err / n
    best = min(sorted(scores), key=lambda lam: scores[lam])
    return best, scores
