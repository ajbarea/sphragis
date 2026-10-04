"""The readings across organizations, against values computed independently with scipy.

Reference values: `scipy.stats.t.ppf`; REML by bounded scalar minimisation of the restricted
log-likelihood (checked on a 2e5-point grid), then the modified HKSJ interval and the
Higgins-Thompson-Spiegelhalter prediction interval written out with numpy. Platform subgroups
are checked against the summary itself on their members.
"""

from __future__ import annotations

import math

import pytest

from sphragis.experiment.across import (
    across_organizations,
    one_sided_p,
    partial_conjunction,
    random_effects,
    t_cdf,
    t_quantile,
)

ORGS = ["a", "b", "c", "d", "e", "f"]
SES = [0.006, 0.005, 0.009, 0.007, 0.004, 0.008]


def _cells(values: list[float], ses: list[float] = SES) -> tuple[dict, dict]:
    names = ORGS[: len(values)]
    return dict(zip(names, values, strict=True)), dict(zip(names, ses[: len(values)], strict=True))


@pytest.mark.parametrize(
    ("p", "df", "expected"),
    [
        (0.975, 1, 12.7062047362),
        (0.975, 2, 4.3026527297),
        (0.9875, 3, 4.1765348461),
        (0.975, 5, 2.5705818356),
        (0.95, 10, 1.8124611228),
        (0.5, 4, 0.0),
        (0.025, 3, -3.1824463053),
    ],
)
def test_t_quantile_matches_scipy(p: float, df: int, expected: float) -> None:
    assert t_quantile(p, df) == pytest.approx(expected, abs=1e-8)
    assert t_cdf(t_quantile(p, df), df) == pytest.approx(p, abs=1e-12)


def test_heterogeneous_summary_matches_reml_and_modified_hksj() -> None:
    estimates, ses = _cells([0.030, 0.004, 0.021, -0.010, 0.009, 0.016])
    out = random_effects(estimates, ses)
    assert out["tau2"] == pytest.approx(0.0001489450273443266, rel=1e-6)
    assert out["estimate"] == pytest.approx(0.011359063302639832, rel=1e-6)
    assert out["se"] == pytest.approx(0.005670136927406267, rel=1e-6)
    assert out["interval"] == pytest.approx((-0.0032164876885214227, 0.02593461429380109), rel=1e-5)
    assert out["prediction"] == pytest.approx(
        (-0.026004036220738144, 0.04872216282601781), rel=1e-5
    )


def test_reml_at_its_boundary_reads_zero_variance() -> None:
    estimates, ses = _cells([0.012, 0.004, 0.021, -0.003, 0.009, 0.016])
    out = random_effects(estimates, ses)
    assert out["tau2"] == 0.0
    assert out["estimate"] == pytest.approx(0.008417645550050638, rel=1e-9)
    assert out["se"] == pytest.approx(0.002732877039420577, rel=1e-9)
    assert out["interval"] == pytest.approx((0.0013925614734885553, 0.015442729626612722), rel=1e-7)


def test_modified_scale_never_narrows_the_interval_below_its_variance() -> None:
    """With near-identical estimates the residual scale is far below one; it is held at one."""
    estimates, ses = _cells([0.010, 0.011, 0.009, 0.010], [0.01, 0.012, 0.009, 0.011])
    out = random_effects(estimates, ses)
    assert out["tau2"] == 0.0
    assert out["se"] == pytest.approx(0.005160223462889998, rel=1e-9)
    assert out["interval"] == pytest.approx((-0.006565957661280417, 0.02627831052654473), rel=1e-7)
    assert out["prediction"] is None


def test_each_platform_gets_the_same_summary_over_its_own_organizations() -> None:
    estimates, ses = _cells([0.030, 0.004, 0.021, -0.010, 0.009, 0.016])
    platforms = {
        "a": "gerrit",
        "b": "gerrit",
        "c": "gerrit",
        "d": "github",
        "e": "github",
        "f": "github",
    }
    out = random_effects(estimates, ses, platforms=platforms)
    assert "moderator" not in out
    for platform, members in (("gerrit", "abc"), ("github", "def")):
        alone = random_effects({o: estimates[o] for o in members}, {o: ses[o] for o in members})
        assert out["subgroups"][platform] == alone


def test_a_platform_with_one_organization_gets_no_summary() -> None:
    estimates, ses = _cells([0.012, 0.004, 0.021, -0.003])
    platforms = {"a": "gerrit", "b": "gerrit", "c": "gerrit", "d": "github"}
    subgroups = random_effects(estimates, ses, platforms=platforms)["subgroups"]
    assert subgroups["github"] == {"organizations": ["d"], "k": 1, "estimate": None}
    assert subgroups["gerrit"]["k"] == 3
    with pytest.raises(ValueError, match="no platform"):
        random_effects(estimates, ses, platforms={"a": "gerrit"})


def test_intervals_are_withheld_below_their_registered_counts() -> None:
    two = random_effects(*_cells([0.012, 0.004]))
    assert two["interval"] is None and two["prediction"] is None
    four = random_effects(*_cells([0.012, 0.004, 0.021, -0.003]))
    assert four["interval"] is not None and four["prediction"] is None


@pytest.mark.parametrize(
    ("estimates", "ses"),
    [
        ({"a": 0.01}, {"a": 0.01}),
        ({"a": 0.01, "b": 0.02}, {"a": 0.01}),
        ({"a": 0.01, "b": 0.02}, {"a": 0.01, "b": 0.0}),
        ({"a": 0.01, "b": math.nan}, {"a": 0.01, "b": 0.01}),
        ({"a": 0.01, "b": 0.02}, {"a": 0.01, "b": math.inf}),
    ],
)
def test_random_effects_refuses_what_it_cannot_summarise(estimates: dict, ses: dict) -> None:
    with pytest.raises(ValueError):
        random_effects(estimates, ses)


def test_partial_conjunction_is_bonferroni_and_monotone() -> None:
    # Sorted 0.001, 0.004, 0.02, 0.3 over k = 4: 4 x 0.001, 3 x 0.004, 2 x 0.02, 1 x 0.3.
    out = partial_conjunction({"a": 0.02, "b": 0.001, "c": 0.3, "d": 0.004}, alpha=0.0125)
    assert out["adjusted"] == pytest.approx([0.004, 0.012, 0.04, 0.3])
    assert out["at_least"] == 2
    # A later Bonferroni product below an earlier one cannot reject r without the r before it.
    out = partial_conjunction({"a": 0.009, "b": 0.009, "c": 0.0001}, alpha=0.0125)
    assert out["adjusted"] == pytest.approx([0.0003, 0.018, 0.018])
    assert out["at_least"] == 1


def test_partial_conjunction_reads_zero_when_nothing_rejects() -> None:
    assert partial_conjunction({"a": 0.5, "b": 0.2}, alpha=0.025)["at_least"] == 0


@pytest.mark.parametrize(
    ("p_values", "alpha"),
    [({}, 0.025), ({"a": 1.2}, 0.025), ({"a": math.nan}, 0.025), ({"a": 0.01}, 0.0)],
)
def test_partial_conjunction_refuses_bad_input(p_values: dict, alpha: float) -> None:
    with pytest.raises(ValueError):
        partial_conjunction(p_values, alpha=alpha)


def test_one_sided_p_counts_draws_at_or_below_zero() -> None:
    assert one_sided_p([0.0, -0.1, 0.2, 0.3]) == 0.5
    with pytest.raises(ValueError):
        one_sided_p([])


def test_across_organizations_reads_both_at_the_step_level() -> None:
    cells = {
        org: {"estimate": e, "bootstrap_se": s, "p_one_sided": p}
        for org, e, s, p in [
            ("a", 0.02, 0.005, 0.0001),
            ("b", 0.01, 0.006, 0.04),
            ("c", 0.015, 0.004, 0.0005),
        ]
    }
    out = across_organizations(cells, confidence=0.975)
    assert out["partial_conjunction"]["alpha"] == pytest.approx(0.0125)
    assert out["partial_conjunction"]["at_least"] == 2
    assert out["random_effects"]["k"] == 3 and out["random_effects"]["confidence"] == 0.975
