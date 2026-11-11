"""Same-game correlation: the Gaussian copula's math and the frozen rho table
(Chat 8 Phase 3, owner decisions Nov 11; docs/correlation.md). Pure: no SQL,
no network, stdlib only (worker RSS: no numpy).

Each leg is an event on a latent standard normal X oriented so that higher
means the YES of an over or of a win. Legs on the SAME latent (one player's
rungs of one stat, one series' spread or total rungs) are merged first into
an interval (lo, hi] on X (combo.py), so a block's latents are distinct and
rho = +-1 never enters a matrix. The joint over a game block is

    k = 1   P(lo < X <= hi), exactly
    k = 2   the bivariate normal CDF (Genz's Drezner-Wesolowsky algorithm,
            tested to 1e-7 against Owen's T), by inclusion-exclusion
    k >= 3  Monte Carlo through a Cholesky factor: 200,000 draws from a seed
            derived from the sorted legs (the same legs give the same
            number), its standard error returned; 1,000,000 draws when the SE
            is over 5% of the estimate.

Sigma comes from the rho table by relationship (pair_rho). A matrix that is
not positive semi-definite is shrunk toward the identity by the smallest
factor that makes it one (psd_shrink), and the factor is logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import NormalDist
from typing import Any, Final, Mapping, Sequence

log = logging.getLogger("valemont.consensus.correlation")

RHO_FILE: Final = Path(__file__).with_name("correlation_rho.json")
N = NormalDist()
INF: Final = math.inf

MC_DRAWS: Final = 200_000
MC_DRAWS_MAX: Final = 1_000_000
#: Escalate when the standard error is over this share of the estimate.
MC_REL_SE: Final = 0.05
#: Cholesky pivots below this count as not positive definite.
PSD_EPS: Final = 1e-10


def Phi(x: float) -> float:
    if x == INF:
        return 1.0
    if x == -INF:
        return 0.0
    return N.cdf(x)


def Phi_inv(p: float) -> float:
    if p <= 0.0:
        return -INF
    if p >= 1.0:
        return INF
    return N.inv_cdf(p)


# ------------------------------------------------------------------ bivariate

_GL: Final = {
    6: ((0.1713244923791705, 0.3607615730481384, 0.4679139345726904),
        (0.9324695142031522, 0.6612093864662647, 0.2386191860831970)),
    12: ((0.04717533638651177, 0.1069393259953183, 0.1600783285433464, 0.2031674267230659,
          0.2334925365383547, 0.2491470458134029),
         (0.9815606342467191, 0.9041172563704750, 0.7699026741943050, 0.5873179542866171,
          0.3678314989981802, 0.1252334085114692)),
    20: ((0.01761400713915212, 0.04060142980038694, 0.06267204833410906, 0.08327674157670475,
          0.1019301198172404, 0.1181945319615184, 0.1316886384491766, 0.1420961093183821,
          0.1491729864726037, 0.1527533871307259),
         (0.9931285991850949, 0.9639719272779138, 0.9122344282513259, 0.8391169718222188,
          0.7463319064601508, 0.6360536807265150, 0.5108670019508271, 0.3737060887154196,
          0.2277858511416451, 0.07652652113349733)),
}


def _bvnu(dh: float, dk: float, r: float) -> float:
    """P(X > dh, Y > dk) for a standard bivariate normal with correlation r:
    Genz (2004), the Drezner-Wesolowsky method with Gauss-Legendre points."""
    if dh == INF or dk == INF:
        return 0.0
    if dh == -INF:
        return Phi(-dk)
    if dk == -INF:
        return Phi(-dh)
    if abs(r) < 0.3:
        w, x = _GL[6]
    elif abs(r) < 0.75:
        w, x = _GL[12]
    else:
        w, x = _GL[20]
    h, k = dh, dk
    hk = h * k
    bvn = 0.0
    if abs(r) < 0.925:
        hs = (h * h + k * k) / 2.0
        asr = math.asin(r)
        for wi, xi in zip(w, x):
            for sgn in (-1.0, 1.0):
                sn = math.sin(asr * (sgn * xi + 1.0) / 2.0)
                bvn += wi * math.exp((sn * hk - hs) / (1.0 - sn * sn))
        bvn = bvn * asr / (4.0 * math.pi) + Phi(-h) * Phi(-k)
    else:
        if r < 0:
            k = -k
            hk = -hk
        if abs(r) < 1.0:
            a2 = (1.0 - r) * (1.0 + r)
            a = math.sqrt(a2)
            bs = (h - k) ** 2
            c = (4.0 - hk) / 8.0
            d = (12.0 - hk) / 16.0
            asr = -(bs / a2 + hk) / 2.0
            if asr > -100.0:
                bvn = a * math.exp(asr) * (1.0 - c * (bs - a2) * (1.0 - d * bs / 5.0) / 3.0 + c * d * a2 * a2 / 5.0)
            if hk > -100.0:
                b = math.sqrt(bs)
                sp = math.sqrt(2.0 * math.pi) * Phi(-b / a)
                bvn -= math.exp(-hk / 2.0) * sp * b * (1.0 - c * bs * (1.0 - d * bs / 5.0) / 3.0)
            a /= 2.0
            for wi, xi in zip(w, x):
                for sgn in (-1.0, 1.0):
                    xs = (a * (sgn * xi + 1.0)) ** 2
                    rs = math.sqrt(1.0 - xs)
                    asr = -(bs / xs + hk) / 2.0
                    if asr > -100.0:
                        sp = 1.0 + c * xs * (1.0 + d * xs)
                        ep = math.exp(-hk * (1.0 - rs) / (2.0 * (1.0 + rs))) / rs
                        bvn += a * wi * math.exp(asr) * (ep - sp)
            bvn = -bvn / (2.0 * math.pi)
        if r > 0:
            bvn += Phi(-max(h, k))
        elif h >= k:
            bvn = -bvn
        else:
            span = Phi(k) - Phi(h) if h < 0 else Phi(-h) - Phi(-k)
            bvn = span - bvn
    return min(1.0, max(0.0, bvn))


def bvn_cdf(h: float, k: float, r: float) -> float:
    """P(X <= h, Y <= k), standard bivariate normal, correlation r."""
    if h == -INF or k == -INF:
        return 0.0
    if h == INF:
        return Phi(k)
    if k == INF:
        return Phi(h)
    return _bvnu(-h, -k, r)


def rect2(lo1: float, hi1: float, lo2: float, hi2: float, r: float) -> float:
    """P(lo1 < X <= hi1, lo2 < Y <= hi2) by inclusion-exclusion."""
    p = bvn_cdf(hi1, hi2, r) - bvn_cdf(lo1, hi2, r) - bvn_cdf(hi1, lo2, r) + bvn_cdf(lo1, lo2, r)
    return min(1.0, max(0.0, p))


# ------------------------------------------------------------------ matrices

def cholesky(s: Sequence[Sequence[float]], eps: float = PSD_EPS) -> list[list[float]] | None:
    """Lower Cholesky factor, or None when a pivot is below eps."""
    n = len(s)
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            v = s[i][j] - sum(lower[i][m] * lower[j][m] for m in range(j))
            if i == j:
                if v < eps:
                    return None
                lower[i][j] = math.sqrt(v)
            else:
                lower[i][j] = v / lower[j][j]
    return lower


def shrunk(s: Sequence[Sequence[float]], lam: float) -> list[list[float]]:
    n = len(s)
    return [[1.0 if i == j else lam * s[i][j] for j in range(n)] for i in range(n)]


def psd_shrink(s: Sequence[Sequence[float]], *, steps: int = 50) -> tuple[list[list[float]], float]:
    """(the matrix to use, the factor its off-diagonals were multiplied by).
    1.0 when it is already positive definite; else the largest lambda in
    [0, 1) for which I + lambda (S - I) is (bisection), i.e. the smallest
    shrink toward 0 that makes it one."""
    if cholesky(s) is not None:
        return [list(r) for r in s], 1.0
    lo, hi = 0.0, 1.0
    for _ in range(steps):
        mid = (lo + hi) / 2.0
        if cholesky(shrunk(s, mid)) is not None:
            lo = mid
        else:
            hi = mid
    log.info("correlation: Sigma (%d legs) not PSD; off-diagonals shrunk by %.6f", len(s), lo)
    return shrunk(s, lo), lo


# ------------------------------------------------------------------ joints

@dataclass(frozen=True)
class Joint:
    p: float
    se: float | None = None
    method: str = "exact"
    draws: int = 0


def seed_of(keys: Sequence[str]) -> int:
    """A fixed seed from the sorted legs: the same legs give the same number."""
    return int.from_bytes(hashlib.sha256("|".join(sorted(keys)).encode()).digest()[:8], "big")


def _mc(lower: Sequence[Sequence[float]], los: Sequence[float], his: Sequence[float], draws: int,
        seed: int) -> tuple[float, float, int]:
    rng = random.Random(seed)
    g = rng.gauss
    k = len(los)
    rows = [lower[i][: i + 1] for i in range(k)]
    hits = 0
    for _ in range(draws):
        e = [g(0.0, 1.0) for _ in range(k)]
        for i in range(k):
            row = rows[i]
            v = 0.0
            for j in range(i + 1):
                v += row[j] * e[j]
            if not (los[i] < v <= his[i]):
                break
        else:
            hits += 1
    p = hits / draws
    return p, math.sqrt(p * (1.0 - p) / draws), hits


def joint(los: Sequence[float], his: Sequence[float], sigma: Sequence[Sequence[float]], keys: Sequence[str], *,
          draws: int = MC_DRAWS, draws_max: int = MC_DRAWS_MAX) -> Joint:
    """P(lo_i < X_i <= hi_i for every i), X ~ N(0, sigma) (sigma already PSD)."""
    k = len(los)
    if k == 0:
        return Joint(1.0)
    if k == 1:
        return Joint(max(0.0, Phi(his[0]) - Phi(los[0])))
    if k == 2:
        return Joint(rect2(los[0], his[0], los[1], his[1], sigma[0][1]), method="bivariate")
    lower = cholesky(sigma)
    if lower is None:  # the caller passes psd_shrink's output; a guard, not a path
        lower = cholesky(shrunk(sigma, 1.0 - 1e-9)) or cholesky(shrunk(sigma, 0.0))
        assert lower is not None
    seed = seed_of(keys)
    p, se, hits = _mc(lower, los, his, draws, seed)
    used = draws
    if (hits == 0 or se > MC_REL_SE * p) and draws_max > draws:
        p, se, hits = _mc(lower, los, his, draws_max, seed)
        used = draws_max
    return Joint(p, se, "monte_carlo", used)


# ------------------------------------------------------------------ the table

def _normalized(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n")


def table_sha256(raw: bytes) -> str:
    return hashlib.sha256(_normalized(raw)).hexdigest()


@dataclass(frozen=True)
class RhoTable:
    data: Mapping[str, Any]
    sha256: str

    def table_for(self, league: str | None, sport: str | None) -> tuple[str | None, bool]:
        """(the table's name, borrowed?) for a league, else the sport (soccer)."""
        t = self.data["tables"]
        name = t.get(league or "") or t.get(sport or "")
        return name, (league or "") in self.data["borrowed_tables"]

    def no_draw(self, league: str | None) -> bool:
        return (league or "") in self.data["no_draw"]

    def kind_of(self, table: str | None, league: str | None, series: str) -> str | None:
        """A prop's kind from its series (PROT stripped, the league prefix
        removed): None for a series the table does not name."""
        if not table:
            return None
        base = series[:-4] if series.endswith("PROT") else series
        prefix = self.data["prefixes"].get(league or "", "")
        if prefix and base.startswith(prefix):
            return self.data["kinds"].get(table, {}).get(base[len(prefix):])
        return None

    def contains(self, table: str | None, a: str, b: str) -> bool:
        """One kind is a sum that holds the other (pts and PRA)."""
        s = self.data["sums"].get(table or "", {})
        return b in s.get(a, ()) or a in s.get(b, ())

    def row(self, table: str | None, key: str) -> tuple[float, str] | None:
        """(rho, source class) of an exact key, or None."""
        r = self.data["rows"].get(table or "", {}).get(key)
        if r is None:
            return None
        return float(r["rho"]), self.data["sources"][r["src"]]["class"]


@lru_cache(maxsize=1)
def load_table(path: str = str(RHO_FILE)) -> RhoTable:
    raw = Path(path).read_bytes()
    return RhoTable(json.loads(raw.decode("utf-8")), table_sha256(raw))


# ------------------------------------------------------------------ relations

@dataclass(frozen=True)
class Role:
    """What one latent is, for the table: a player's stat, a club's line, the
    game total, or something the table cannot place (``kind`` None)."""

    line: str | None = None        # win | cover | over | btts | None for a player
    kind: str | None = None        # a player's kind (pts, rec_yds, ...)
    club: str | None = None        # the game's code of the player's / line's club
    player: str | None = None      # the player's key (team + name)

    @property
    def is_player(self) -> bool:
        return self.player is not None


class Refused(Exception):
    """An impossible or unpriceable combination: never priced."""


@dataclass(frozen=True)
class PairRho:
    rho: float
    cls: str            # measured_external | prior | borrowed | unmodeled
    key: str | None


UNMODELED = "unmodeled"
#: A player's line roles that take the player's own club rows.
CLUB_LINES: Final = ("win", "cover")


def _with_borrow(found: tuple[float, str] | None, borrowed: bool) -> tuple[float, str] | None:
    if found is None:
        return None
    return found[0], ("borrowed" if borrowed else found[1])


def pair_rho(t: RhoTable, table: str | None, borrowed: bool, no_draw: bool, a: Role, b: Role) -> PairRho:
    """rho between two latents of one game, by relationship (owner, Nov 11).
    Raises Refused for one player's stat beside a sum that holds it."""
    if table is None:
        return PairRho(0.0, UNMODELED, None)

    def look(key: str, *, borrow: bool = False) -> tuple[float, str] | None:
        return _with_borrow(t.row(table, key), borrowed or borrow)

    # ---- two players
    if a.is_player and b.is_player:
        if a.kind is None or b.kind is None:
            return PairRho(0.0, UNMODELED, None)
        if a.player == b.player:
            if t.contains(table, a.kind, b.kind):
                raise Refused(f"{a.kind} and {b.kind} for one player: not jointly gaussian; pick one")
            rel = "self"
        elif a.club is None or b.club is None:
            return PairRho(0.0, UNMODELED, None)
        else:
            rel = "mate" if a.club == b.club else "opp"
        x, y = sorted((a.kind, b.kind))
        key = f"{rel}|{x}|{y}"
        found = look(key)
        return PairRho(found[0], found[1], key) if found else PairRho(0.0, UNMODELED, key)

    # ---- a player and a game line
    if a.is_player or b.is_player:
        p, g = (a, b) if a.is_player else (b, a)
        if p.kind is None or g.line is None:
            return PairRho(0.0, UNMODELED, None)
        if g.line == "over":
            key = f"team|{p.kind}|over"
            found = look(key)
            return PairRho(found[0], found[1], key) if found else PairRho(0.0, UNMODELED, key)
        if g.line not in CLUB_LINES or p.club is None or g.club is None:
            return PairRho(0.0, UNMODELED, None)

        def club_row(rel: str) -> tuple[float, str, str] | None:
            key = f"{rel}|{p.kind}|{g.line}"
            found = look(key)
            if found:
                return found[0], found[1], key
            if g.line == "cover":  # decision 2: the moneyline's row, borrowed
                key = f"{rel}|{p.kind}|win"
                found = look(key, borrow=True)
                if found:
                    return found[0], found[1], key
            return None

        if p.club == g.club:
            r = club_row("team")
        else:
            r = club_row("oppteam")
            if r is None and no_draw:  # decision 3: the other club's line is own NO
                own = club_row("team")
                r = (-own[0], own[1], own[2]) if own else None
        return PairRho(r[0], r[1], r[2]) if r else PairRho(0.0, UNMODELED, None)

    # ---- two game lines
    if a.line is None or b.line is None:
        return PairRho(0.0, UNMODELED, None)
    x, y = sorted((a.line, b.line))
    if {x, y} <= set(CLUB_LINES):
        if a.club is None or b.club is None:
            return PairRho(0.0, UNMODELED, None)
        key = f"game|{x}|{y}"
        found = look(key)
        if found is None:
            return PairRho(0.0, UNMODELED, key)
        if a.club == b.club:
            return PairRho(found[0], found[1], key)
        if no_draw:
            return PairRho(-found[0], found[1], key)
        return PairRho(0.0, UNMODELED, key)
    # One club line and the total (or btts): club-agnostic.
    key = f"game|{x}|{y}"
    found = look(key)
    if found is None and "cover" in (x, y):
        other = y if x == "cover" else x
        bx, by = sorted(("win", other))
        key = f"game|{bx}|{by}"
        found = look(key, borrow=True)
    return PairRho(found[0], found[1], key) if found else PairRho(0.0, UNMODELED, key)
