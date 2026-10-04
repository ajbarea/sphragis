"""Readings beside the pass rule, across organizations; none binds a verdict.

How many organizations show H1 (the partial conjunction, Benjamini and Heller, Biometrics 2008)
and how much the effect varies between them (a random-effects summary: REML for the
between-organization variance, the modified Hartung-Knapp-Sidik-Jonkman interval of Röver, Knapp
and Friede, BMC MRM 2015, a prediction interval from five organizations, each platform's
subgroup beside it). Registered in `docs/registered-decisions.md`, "Readings beside the pass rule".
Standard library only, like the rest of the measurement package.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

# The Cochrane Handbook (6.5, chapter 10) uses the HKSJ interval only above two studies and a
# prediction interval from about five; both thresholds are registered with the reading.
MIN_FOR_INTERVAL = 3
MIN_FOR_PREDICTION = 5
# A platform's subgroup summary needs two organizations, as the summary does.
MIN_PER_PLATFORM = 2


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """The continued fraction of the regularized incomplete beta (modified Lentz)."""
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        for numerator in (
            m * (b - m) * x / ((a + m2 - 1.0) * (a + m2)),
            -(a + m) * (a + b + m) * x / ((a + m2) * (a + m2 + 1.0)),
        ):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + numerator / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            return h
    raise ArithmeticError("incomplete beta did not converge")


def _regularized_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def t_cdf(x: float, df: int) -> float:
    """Student's t distribution function."""
    if df < 1:
        raise ValueError(f"t needs at least one degree of freedom, got {df}")
    tail = 0.5 * _regularized_beta(df / 2.0, 0.5, df / (df + x * x))
    return 1.0 - tail if x >= 0 else tail


def t_quantile(p: float, df: int) -> float:
    """Student's t quantile, by bisection on `t_cdf` to 1e-12."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"a quantile needs p in (0, 1), got {p}")
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -t_quantile(1.0 - p, df)
    low, high = 0.0, 1.0
    while t_cdf(high, df) < p:
        low, high = high, high * 2.0
    while high - low > 1e-12 * max(1.0, high):
        mid = 0.5 * (low + high)
        if t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting, for the one- or two-column designs used here."""
    n = len(vector)
    rows = [list(row) + [value] for row, value in zip(matrix, vector, strict=True)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-300:
            raise ArithmeticError("singular design")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(n):
            if r != col:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col], strict=True)]
    return [rows[i][n] / rows[i][i] for i in range(n)]


def _inverse(matrix: list[list[float]]) -> list[list[float]]:
    n = len(matrix)
    columns = [_solve(matrix, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[columns[j][i] for j in range(n)] for i in range(n)]


def _weighted_fit(
    y: Sequence[float], v: Sequence[float], x: Sequence[Sequence[float]], tau2: float
) -> dict[str, Any]:
    """Generalized least squares at a given between-organization variance."""
    w = [1.0 / (vi + tau2) for vi in v]
    p = len(x[0])
    xtwx = [
        [sum(wi * xi[a] * xi[b] for wi, xi in zip(w, x, strict=True)) for b in range(p)]
        for a in range(p)
    ]
    xtwy = [sum(wi * xi[a] * yi for wi, xi, yi in zip(w, x, y, strict=True)) for a in range(p)]
    coef = _solve(xtwx, xtwy)
    residual = [
        yi - sum(c * xa for c, xa in zip(coef, xi, strict=True))
        for yi, xi in zip(y, x, strict=True)
    ]
    return {"w": w, "xtwx": xtwx, "coef": coef, "residual": residual}


def _restricted_nll(
    y: Sequence[float], v: Sequence[float], x: Sequence[Sequence[float]], tau2: float
) -> float:
    """Negative restricted log-likelihood of the between-organization variance, up to a constant."""
    fit = _weighted_fit(y, v, x, tau2)
    xtwx = fit["xtwx"]
    if len(xtwx) > 2:
        raise ValueError(f"designs of more than two columns are not supported, got {len(xtwx)}")
    det = xtwx[0][0] if len(xtwx) == 1 else xtwx[0][0] * xtwx[1][1] - xtwx[0][1] * xtwx[1][0]
    quadratic = sum(wi * ri * ri for wi, ri in zip(fit["w"], fit["residual"], strict=True))
    return 0.5 * (sum(math.log(vi + tau2) for vi in v) + math.log(det) + quadratic)


# Grid density for the REML search: points per decade over the twelve decades below the bound.
_GRID_PER_DECADE = 50
_GRID_DECADES = 12
_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0


def _reml_tau2(y: Sequence[float], v: Sequence[float], x: Sequence[Sequence[float]]) -> float:
    """REML between-organization variance, its global maximum on [0, bound].

    The restricted likelihood can have two peaks, and Fisher scoring from zero stops at the
    nearer one or oscillates against the zero boundary. So the likelihood is read on zero and a
    log grid below a bound ten times the larger of the estimates' squared range and their largest
    variance, the bound doubled while the best point sits on it, and the best grid point is
    refined by golden-section search between its neighbours.
    """
    nll = lambda t: _restricted_nll(y, v, x, t)  # noqa: E731
    spread = (max(y) - min(y)) ** 2
    bound = 10.0 * max(spread, max(v))
    for _ in range(60):
        steps = _GRID_PER_DECADE * _GRID_DECADES
        grid = [0.0] + [
            bound * 10.0 ** (_GRID_DECADES * (i / steps - 1.0)) for i in range(steps + 1)
        ]
        values = [nll(t) for t in grid]
        best = min(range(len(grid)), key=values.__getitem__)
        if best < len(grid) - 1:
            break
        bound *= 2.0
    else:
        raise ArithmeticError("REML maximum lies beyond any bound tried")
    if best == 0:
        return 0.0
    low, high = grid[best - 1], grid[best + 1]
    a, b = high - _GOLDEN * (high - low), low + _GOLDEN * (high - low)
    fa, fb = nll(a), nll(b)
    while high - low > 1e-10 * high:
        if fa < fb:
            high, b, fb = b, a, fa
            a = high - _GOLDEN * (high - low)
            fa = nll(a)
        else:
            low, a, fa = a, b, fb
            b = low + _GOLDEN * (high - low)
            fb = nll(b)
    candidate = 0.5 * (low + high)
    return min((0.0, grid[best], candidate), key=nll)


def random_effects(
    estimates: Mapping[str, float],
    standard_errors: Mapping[str, float],
    *,
    confidence: float = 0.95,
    platforms: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The random-effects summary of each organization's H1 estimate.

    Every organization needs an estimate and a positive standard error. The mean's interval is
    the modified HKSJ (the residual scale truncated at one, Röver et al. 2015) on k - 1 degrees of
    freedom, given only from `MIN_FOR_INTERVAL` organizations; the prediction interval adds the
    between-organization variance on k - 2 degrees of freedom (Higgins, Thompson and Spiegelhalter
    2009), only from `MIN_FOR_PREDICTION`, and under-covers when that variance is small (Partlett
    and Riley, Stat. Med. 2017). With `platforms`, each platform with at least `MIN_PER_PLATFORM`
    organizations gets the same summary over its own, descriptive: no moderator is tested, since
    meta-regression needs about ten studies (Cochrane Handbook 6.5, chapter 10).
    """
    orgs = sorted(estimates)
    if sorted(standard_errors) != orgs:
        raise ValueError("every organization needs both an estimate and a standard error")
    if len(orgs) < 2:
        raise ValueError(f"a summary across organizations needs at least two, got {len(orgs)}")
    for org in orgs:
        se = standard_errors[org]
        variance = se * se if math.isfinite(se) else math.inf
        if not (0.0 < variance < math.inf) or not math.isfinite(estimates[org]):
            raise ValueError(f"{org}: estimate {estimates[org]!r}, standard error {se!r}")
    y = [estimates[o] for o in orgs]
    v = [standard_errors[o] ** 2 for o in orgs]
    k = len(orgs)
    tail = 1.0 - (1.0 - confidence) / 2.0

    summary = _summary(y, v, [[1.0] for _ in orgs], tail=tail)
    mean = summary["coef"][0]
    result: dict[str, Any] = {
        "organizations": orgs,
        "k": k,
        "confidence": confidence,
        "tau2": summary["tau2"],
        "estimate": mean,
        "se": summary["se"][0] if k >= MIN_FOR_INTERVAL else None,
        "interval": summary["interval"][0] if k >= MIN_FOR_INTERVAL else None,
        "prediction": None,
    }
    if k >= MIN_FOR_PREDICTION:
        half = t_quantile(tail, k - 2) * math.sqrt(summary["tau2"] + summary["se"][0] ** 2)
        result["prediction"] = (mean - half, mean + half)
    if platforms is not None:
        missing = sorted(set(orgs) - set(platforms))
        if missing:
            raise ValueError(f"no platform for {missing}")
        result["subgroups"] = {}
        for platform in sorted({platforms[o] for o in orgs}):
            members = [o for o in orgs if platforms[o] == platform]
            result["subgroups"][platform] = (
                random_effects(
                    {o: estimates[o] for o in members},
                    {o: standard_errors[o] for o in members},
                    confidence=confidence,
                )
                if len(members) >= MIN_PER_PLATFORM
                else {"organizations": members, "k": len(members), "estimate": None}
            )
    return result


def _summary(
    y: Sequence[float], v: Sequence[float], x: Sequence[Sequence[float]], *, tail: float
) -> dict[str, Any]:
    tau2 = _reml_tau2(y, v, x)
    fit = _weighted_fit(y, v, x, tau2)
    k, p = len(y), len(x[0])
    scale = sum(wi * ri * ri for wi, ri in zip(fit["w"], fit["residual"], strict=True)) / (k - p)
    inv = _inverse(fit["xtwx"])
    se = [math.sqrt(max(1.0, scale) * inv[a][a]) for a in range(p)]
    quantile = t_quantile(tail, k - p)
    return {
        "tau2": tau2,
        "coef": fit["coef"],
        "se": se,
        "scale": scale,
        "interval": [
            (c - quantile * s, c + quantile * s) for c, s in zip(fit["coef"], se, strict=True)
        ],
    }


def partial_conjunction(p_values: Mapping[str, float], *, alpha: float) -> dict[str, Any]:
    """The largest r for which "at least r of the k organizations show the effect" is rejected.

    Bonferroni form (Benjamini and Heller 2008): the r-th smallest one-sided p-value times
    k - r + 1, made non-decreasing in r so that rejecting r implies rejecting every smaller r.
    Valid under any dependence between the cells.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if not p_values:
        raise ValueError("a partial conjunction needs at least one organization")
    for org, p in p_values.items():
        if not (math.isfinite(p) and 0.0 <= p <= 1.0):
            raise ValueError(f"{org}: p-value {p!r}")
    ordered = sorted(p_values.values())
    k = len(ordered)
    adjusted, running = [], 0.0
    for r, p in enumerate(ordered, start=1):
        running = max(running, min(1.0, (k - r + 1) * p))
        adjusted.append(running)
    # Rounded so float noise cannot decide a tie: (1 - 0.975) / 2 is 0.012500000000000011, which
    # would count a p-value of exactly 0.0125 as below it.
    level = round(alpha, 12)
    largest = max((r for r, p in enumerate(adjusted, start=1) if round(p, 12) < level), default=0)
    return {"k": k, "alpha": alpha, "adjusted": adjusted, "at_least": largest}


def one_sided_p(draws: Sequence[float]) -> float:
    """One-sided bootstrap p-value for an effect above zero: the share of draws at or below zero."""
    if not draws:
        raise ValueError("no bootstrap draws")
    return sum(1 for d in draws if d <= 0.0) / len(draws)


def across_organizations(
    cells: Mapping[str, Mapping[str, Any]],
    *,
    confidence: float,
    platforms: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Both readings over H1 cells from `h1_over_partitions`, at one Holm step's confidence.

    The partial conjunction is read at that step's one-sided level, the cells' own; the summary's
    interval at the same confidence. A cell's p-value falls below the level exactly when its
    percentile interval's lower bound lies above zero only when its resample count times the level
    is a whole number (10,000 draws at 0.0125 or 0.025), so any other count is refused.
    """
    alpha = (1.0 - confidence) / 2.0
    for org, cell in cells.items():
        excluded = cell["resamples"] * alpha
        if abs(excluded - round(excluded)) > 1e-9 * max(1.0, excluded):
            raise ValueError(
                f"{org}: {cell['resamples']} resamples at one-sided {alpha:g} put the interval's "
                "bound between draws, so its p-value and its verdict could disagree"
            )
    return {
        "partial_conjunction": partial_conjunction(
            {org: cell["p_one_sided"] for org, cell in cells.items()}, alpha=alpha
        ),
        "random_effects": random_effects(
            {org: cell["estimate"] for org, cell in cells.items()},
            {org: cell["bootstrap_se"] for org, cell in cells.items()},
            confidence=confidence,
            platforms=platforms,
        ),
    }
