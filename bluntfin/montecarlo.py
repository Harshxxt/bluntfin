"""Monte Carlo retirement simulation.

Each path draws one real (inflation-adjusted) return per year from a lognormal
distribution whose arithmetic mean and standard deviation match the inputs.
Volatility drag is therefore real: the median outcome is below the straight-line
projection that uses the same average return. That is the point.

Model, per path:
  accumulation: balance = balance * growth + annual contribution   (until retirement)
  retirement:   balance -= annual withdrawal, then balance *= growth (until end age)
A path fails the first year the balance cannot cover the withdrawal.

Not modelled: fat tails beyond lognormal, return autocorrelation, varying spending,
Social Security / pensions, fees (subtract them from the mean yourself).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class MCResult:
    runs: int
    success_rate: float              # share of paths that never ran out (0-1)
    at_retirement: dict[str, float]  # p10/p50/p90 balance at retirement
    at_end: dict[str, float]         # p10/p50/p90 balance at end age (0 if depleted)
    median_depletion_age: float | None  # among failed paths
    p10_depletion_age: float | None     # the bad-luck case: 10th percentile of all paths


class Simulator:
    """Pre-draws return paths once, so different contribution levels are compared on
    the same simulated markets (common random numbers). This keeps the solver monotone."""

    def __init__(self, years_to_ret: int, years_in_ret: int, mean_pct: float, stdev_pct: float,
                 runs: int = 5000, seed: int | None = None):
        if runs < 100:
            raise ValueError("Use at least 100 runs; fewer is noise, not a simulation.")
        self.years_to_ret = max(years_to_ret, 0)
        self.years_in_ret = max(years_in_ret, 0)
        self.runs = runs
        m, s = mean_pct / 100, stdev_pct / 100
        if 1 + m <= 0:
            raise ValueError("Mean return must be above -100%.")
        sigma2 = math.log(1 + (s * s) / ((1 + m) ** 2))
        mu = math.log(1 + m) - sigma2 / 2
        sigma = math.sqrt(sigma2)
        rng = random.Random(seed)
        n = self.years_to_ret + self.years_in_ret
        self.paths = [[math.exp(rng.gauss(mu, sigma)) for _ in range(n)] for _ in range(runs)]

    def run(self, start: float, annual_contrib: float, annual_withdrawal: float,
            retirement_age: int) -> MCResult:
        at_ret, at_end, depleted_ages = [], [], []
        all_ages = []  # depletion age, or +inf for success
        T, R = self.years_to_ret, self.years_in_ret
        for growth in self.paths:
            bal = start
            for y in range(T):
                bal = bal * growth[y] + annual_contrib
            at_ret.append(bal)
            failed_at = None
            for y in range(R):
                if bal < annual_withdrawal:
                    failed_at = retirement_age + y
                    bal = 0.0
                    break
                bal = (bal - annual_withdrawal) * growth[T + y]
            at_end.append(bal)
            if failed_at is None:
                all_ages.append(math.inf)
            else:
                depleted_ages.append(failed_at)
                all_ages.append(failed_at)
        success = 1 - len(depleted_ages) / self.runs
        p10_age = _pctl(all_ages, 10)
        return MCResult(
            runs=self.runs,
            success_rate=success,
            at_retirement={k: _pctl(at_ret, q) for k, q in (("p10", 10), ("p50", 50), ("p90", 90))},
            at_end={k: _pctl(at_end, q) for k, q in (("p10", 10), ("p50", 50), ("p90", 90))},
            median_depletion_age=_pctl(depleted_ages, 50) if depleted_ages else None,
            p10_depletion_age=None if math.isinf(p10_age) else p10_age,
        )

    def contribution_for(self, target_success: float, start: float, annual_withdrawal: float,
                         retirement_age: int, max_annual: float = 1e9) -> float | None:
        """Smallest annual contribution reaching `target_success` (0-1). None if unreachable."""
        if self.years_to_ret == 0:
            ok = self.run(start, 0, annual_withdrawal, retirement_age).success_rate >= target_success
            return 0.0 if ok else None
        if self.run(start, 0, annual_withdrawal, retirement_age).success_rate >= target_success:
            return 0.0
        hi = max(annual_withdrawal, 1.0)
        while self.run(start, hi, annual_withdrawal, retirement_age).success_rate < target_success:
            hi *= 2
            if hi > max_annual:
                return None
        lo = 0.0
        for _ in range(30):
            mid = (lo + hi) / 2
            if self.run(start, mid, annual_withdrawal, retirement_age).success_rate >= target_success:
                hi = mid
            else:
                lo = mid
            if hi - lo < 1:
                break
        return hi


def _pctl(values: list[float], q: float) -> float:
    xs = sorted(values)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * q / 100
    f = math.floor(k)
    c = min(f + 1, len(xs) - 1)
    if xs[f] == xs[c] or math.isinf(xs[f]) or math.isinf(xs[c]):
        return xs[f] if k - f < 0.5 else xs[c]
    return xs[f] + (xs[c] - xs[f]) * (k - f)
