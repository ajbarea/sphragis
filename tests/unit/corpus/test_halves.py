from __future__ import annotations

from typing import Any

import pytest

from sphragis.corpus.halves import (
    excluded_projects,
    halves,
    runner_train,
    split_criteria,
    suffix,
    total_variation,
)

WINDOW = ("2024-11-01", "2025-09-01")


def _rows(spec: dict[str, list[str]], created: str = "2025-01-15") -> list[dict[str, Any]]:
    """One row per path, grouped by project."""
    return [
        {"project": project, "path": path, "created": f"{created} 00:00:00"}
        for project, paths in spec.items()
        for path in paths
    ]


def _balanced(ext_a: str = "py", ext_b: str = "py", n: int = 10) -> list[dict[str, Any]]:
    """Six projects of equal size, so the greedy rule alternates sides: p0, p2, p4 against
    p1, p3, p5. Half a's projects write `ext_a` and half b's `ext_b`."""
    side_a = {"p0", "p2", "p4"}
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
    result = _criteria(split_by_language, ref)
    assert result["suffix_total_variation"] == 1.0
    checks = result["checks"]
    assert checks["language_mix"] is False and checks["size"] and checks["projects"]


def _half(projects: int, size: int, share: float, suffixes: dict[str, int] | None = None) -> dict:
    return {
        "assigned_projects": projects,
        "projects": projects,
        "train_examples": size,
        "largest": "p",
        "largest_share": share,
        "suffixes": suffixes or {".py": size},
    }


REF = [_half(5, 100, 0.3), _half(5, 120, 0.3, {".py": 60, ".rst": 60})]  # floor 100, TV 0.5


@pytest.mark.parametrize(
    ("own", "check", "expected"),
    [
        ([_half(3, 100, 0.3), _half(3, 100, 0.3)], "projects", True),
        ([_half(3, 100, 0.3), _half(2, 100, 0.3)], "projects", False),
        ([_half(3, 100, 0.5), _half(3, 100, 0.3)], "largest_share", True),
        ([_half(3, 100, 0.3), _half(3, 100, 0.51)], "largest_share", False),
        ([_half(3, 100, 0.3), _half(3, 150, 0.3)], "size", True),
        ([_half(3, 150, 0.3), _half(3, 99, 0.3)], "size", False),
    ],
)
def test_each_threshold_holds_at_its_boundary_and_fails_one_past_it_in_either_half(
    own: list[dict], check: str, expected: bool
) -> None:
    assert split_criteria(own, REF)["checks"][check] is expected


def test_the_size_floor_is_the_reference_s_smaller_half() -> None:
    assert split_criteria([_half(3, 110, 0.3), _half(3, 110, 0.3)], REF)["size_floor"] == 100


def test_the_language_mix_ceiling_holds_at_equality() -> None:
    own = [_half(3, 100, 0.3), _half(3, 100, 0.3, {".py": 50, ".rst": 50})]
    result = split_criteria(own, REF)
    assert result["suffix_total_variation"] == result["suffix_total_variation_ceiling"] == 0.5
    assert result["checks"]["language_mix"] is True


def test_a_half_with_no_training_rows_does_not_qualify() -> None:
    own = [_half(3, 100, 0.3), {**_half(0, 0, 1.0), "suffixes": {}}]
    result = split_criteria(own, REF)
    assert result["suffix_total_variation"] is None
    assert result["qualifies"] is False


def test_the_assignment_balances_the_built_rows_not_what_survives_dedup() -> None:
    built = _rows({"big": [f"b{j}.py" for j in range(10)], "x": ["x.py"] * 6, "y": ["y.py"] * 5})
    # Built: big 10 | x 6 + y 5. After a dedup that left big with 1 row, balancing the survivors
    # would put big with x; the registered rule still reads the built counts.
    train = [r for r in built if r["project"] != "big"] + [built[0]]
    out = halves(built, train, WINDOW)
    assert [h["assigned_projects"] for h in out] == [1, 2]
    assert [h["train_examples"] for h in out] == [1, 11]


def test_the_window_end_is_exclusive() -> None:
    inside = _balanced()
    on_end = _rows({"late": ["z.py"] * 50}, created=WINDOW[1])
    assert halves(inside + on_end, inside, WINDOW) == halves(inside, inside, WINDOW)
    assert excluded_projects(inside + on_end, WINDOW) == ["late"]


def test_runner_train_dedups_each_half_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    import sphragis.corpus.pipeline as pipeline

    seen: list[set[str]] = []

    def dedup(rows: list[dict]) -> tuple[list[dict], dict]:
        seen.append({r["project"] for r in rows})
        return rows, {}

    def split(rows: list[dict], windows: dict) -> tuple[dict, dict, dict]:
        return {"train": rows}, {}, {}

    monkeypatch.setattr(pipeline, "run_dedup", dedup)
    monkeypatch.setattr(pipeline, "run_split", split)
    built = _balanced()
    train = runner_train(built, {"train": WINDOW})
    assert seen == [{"p0", "p2", "p4"}, {"p1", "p3", "p5"}]
    assert len(train) == len(built)
