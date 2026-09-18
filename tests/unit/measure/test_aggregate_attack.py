"""The project-level permutation test in the aggregate attack."""

from __future__ import annotations

import ast
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "aggregate_attack.py"


def _permutation_block() -> ast.FunctionDef:
    tree = ast.parse(_SCRIPT.read_text())
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")


def test_the_true_grouping_is_scored_by_the_same_call_as_every_relabeling() -> None:
    """Both sides must be one statistic, or the true labelling can score below itself.

    It did: the observed AUC was read from the eight-split, full-draw run above while each
    relabeling was scored at two splits and a quarter of the draws, and the test reported
    p = 0.000, which an enumeration of 84 arrangements cannot produce.
    """
    source = _SCRIPT.read_text()
    calls = source.count("permutation_auc(")
    assert calls >= 3, "one helper, called for the truth and for each arrangement"
    assert "observed = permutation_auc(truth)" in source
    assert 'observed = cell["mixed_rounds"]' not in source, (
        "the observed statistic must not come from a run with different settings"
    )


def test_the_enumeration_is_checked_to_contain_the_true_grouping() -> None:
    """If it does not, the arrangements are not the null the p-value claims, and p can be 0."""
    source = _SCRIPT.read_text()
    assert "saw_truth" in source
    assert "raise RuntimeError" in source


def test_the_reported_p_cannot_fall_below_one_over_the_arrangements() -> None:
    """The true grouping counts itself, so the floor is 1/usable by construction."""
    import json

    for path in sorted(Path("datasets/results").glob("aggregate-attack-*.json")):
        report = json.loads(path.read_text())
        for label, cell in report.get("project_permutation", {}).items():
            usable, value = cell["relabelings"], cell["p"]
            if value is None:
                continue
            assert value >= 1.0 / usable - 1e-12, f"{path.name}:{label} reports p {value}"
            assert cell["at_least_as_extreme"] >= 1
