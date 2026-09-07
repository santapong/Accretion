"""Exact rank check, not an empirical evaluation of SCAPE or Accretion.

Under exchangeable, distinct residuals and a fixed score function, the next
residual has a uniform rank among n + 1 residuals. Enumerating those ranks
shows why clamping an out-of-range conformal rank loses nominal coverage.
"""

from fractions import Fraction
from math import ceil
import json


def rank_case(n: int, alpha: Fraction) -> dict:
    required_rank = ceil((n + 1) * (1 - alpha))
    clamped_rank = min(required_rank, n)
    clamped_covered = sum(rank <= clamped_rank for rank in range(1, n + 2))
    conservative_covered = (
        n + 1 if required_rank > n
        else sum(rank <= required_rank for rank in range(1, n + 2))
    )
    coverage = Fraction(clamped_covered, n + 1)
    conservative = Fraction(conservative_covered, n + 1)
    assert conservative >= 1 - alpha
    return {
        "calibration_n": n,
        "target_coverage": float(1 - alpha),
        "required_rank": required_rank,
        "clamped_rank": clamped_rank,
        "clamped_exact_coverage": float(coverage),
        "conservative_exact_coverage": float(conservative),
        "needs_full_support_set": required_rank > n,
    }


if __name__ == "__main__":
    cases = [rank_case(n, Fraction(1, 20)) for n in (6, 15, 19, 99)]
    assert cases[1]["clamped_exact_coverage"] == 0.9375
    assert cases[1]["clamped_exact_coverage"] < cases[1]["target_coverage"]
    assert cases[2]["clamped_exact_coverage"] == 0.95
    print(json.dumps({"scope": "EXACT_MATHEMATICAL_CHECK_ONLY", "cases": cases}, indent=2))
