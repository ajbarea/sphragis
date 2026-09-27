from __future__ import annotations

from typing import Any

import pytest

from sphragis.corpus.halves import halves, split_criteria, suffix, total_variation

WINDOW = ("2024-11-01", "2025-09-01")


def _rows(spec: dict[str, list[str]], created: str = "2025-01-15") -> list[dict[str, Any]]:
    """One row per path, grouped by project."""
    return [
        {"project": project, "path": path, "created": f"{created} 00:00:00"}
        for project, paths in spec.items()
        for path in paths
    ]


def _balanced(ext_a: str = "py", ext_b: str = "py", n: int = 10) -> list[dict[str, Any]]:
    """Six projects of equal size, so the greedy rule alternates sides: p0, p3, p4 against
    p1, p2, p5. Half a's projects write `ext_a` and half b's `ext_b`."""
    side_a = {"p0", "p3", "p4"}
    return _rows(
        {
            f"p{i}": [f"f{j}.{ext_a if f'p{i}' in side_a else ext_b}" for j in range(n)]
            for i in range(6)
        }
    )


def test_suffix_is_the_lowercased_suffix_or_the_name() -> None:
    assert suffix("src/A.CPP") == ".cpp"
    assert suffix("tools/Makefile") == "Makefile"
    assert suffix("a/b.tar.gz") == ".gz"


def test_total_variation_is_half_the_l1_distance_of_the_distributions() -> None:
    assert total_variation({"a": 1}, {"a": 5}) == 0.0
    assert total_variation({"a": 1}, {"b": 1}) == 1.0
    assert total_variation({"a": 3, "b": 1}, {"a": 1, "b": 1}) == pytest.approx(0.25)
    with pytest.raises(ValueError):
        total_variation({}, {"a": 1})


def test_sides_are_assigned_on_the_built_rows_and_measured_on_the_train_rows() -> None:
    built = _balanced()
    train = [row for row in built if row["path"] != "f0.py"]  # dedup dropped one a project
    out = halves(built, train, WINDOW)
    assert [h["projects"] for h in out] == [3, 3]
    assert [h["train_examples"] for h in out] == [27, 27]
    assert out[0]["largest_share"] == pytest.approx(1 / 3)
    assert out[0]["suffixes"] == {".py": 27}


def test_rows_outside_the_window_do_not_drive_the_assignment() -> None:
    inside = _balanced()
    outside = _rows({"p5": [f"g{j}.py" for j in range(100)]}, created="2025-10-01")
    assert halves(inside + outside, inside, WINDOW) == halves(inside, inside, WINDOW)


def _criteria(own_rows: list[dict[str, Any]], ref_rows: list[dict[str, Any]]) -> dict:
    return split_criteria(halves(own_rows, own_rows, WINDOW), halves(ref_rows, ref_rows, WINDOW))


def test_a_split_like_the_reference_qualifies() -> None:
    result = _criteria(_balanced(), _balanced())
    assert result["checks"] == dict.fromkeys(
        ("projects", "largest_share", "size", "language_mix"), True
    )
    assert result["qualifies"]


def test_each_criterion_fails_on_its_own() -> None:
    ref = _balanced(n=10)
    too_few = _rows({"p0": [f"f{j}.py" for j in range(30)], "p1": [f"f{j}.py" for j in range(30)]})
    assert _criteria(too_few, ref)["checks"]["projects"] is False

    dominated = _rows(
        {"big": [f"f{j}.py" for j in range(100)]}
        | {f"s{i}": [f"f{j}.py" for j in range(10)] for i in range(12)}
    )
    assert _criteria(dominated, ref)["checks"]["largest_share"] is False

    small = _balanced(n=5)
    checks = _criteria(small, ref)["checks"]
    assert checks["size"] is False and checks["projects"] and checks["language_mix"]

    split_by_language = _balanced(ext_a="php", ext_b="pp")
    checks = _criteria(split_by_language, ref)["checks"]
    assert checks["language_mix"] is False and checks["size"] and checks["projects"]
