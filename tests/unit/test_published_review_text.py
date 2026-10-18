"""No tracked result from a host opened by written grant carries the model's predictions.

A generation run's `prediction` is model text, and a prediction can repeat its example's review
comment word for word. Qt and Chromium opened their review hosts by written grant on the
condition that no review text is published (`docs/protocol.md`), so their generation runs stay
out of git (`.gitignore`) and only their likelihood rescorings, ids and scores, are committed
(research log, 2026-10-18).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: An organization token in a result's file name, for the hosts opened by written grant.
GRANTED = re.compile(r"(?:^|[-_])(?:qt|qtfull|chromium)(?:[-_.]|$)")

#: Committed before the rule, from Qt's records held from before its grant. Whether they keep
#: their predictions is AJ's call (ROADMAP, "predictions in published generation runs").
BEFORE_THE_RULE = frozenset(
    {
        "projects-qt-creator_qt-creator-qt_qtbase-n788.json",
        "projects-qt-creator_qt-creator-qt_qtbase.json",
        "projects-qt-creator_qt-creator-qt_qtdeclarative.json",
        "projects-qt_qtbase-qt_qtdeclarative.json",
        "rq1-placebo-qt-s2.json",
        "rq1-placebo-qt-s3.json",
        "rq1-placebo-qt.json",
        "rq1-windows-qtfull-fp32-s2.json",
        "rq1-windows-qtfull-fp32-s3.json",
        "rq1-windows-qtfull-fp32.json",
        "rq1-windows-qtfull-s2.json",
        "rq1-windows-qtfull-s3.json",
        "rq1-windows-qtfull.json",
    }
)


def _tracked_results() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "datasets/results"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ROOT / line for line in out.splitlines() if line.endswith(".json")]


def test_no_granted_host_result_carries_predictions() -> None:
    offending = [
        path.name
        for path in _tracked_results()
        if GRANTED.search(path.name)
        and path.name not in BEFORE_THE_RULE
        and '"prediction"' in path.read_text()
    ]
    assert not offending, f"granted-host results carrying predictions: {offending}"


def test_the_listed_exceptions_still_exist_and_still_carry_predictions() -> None:
    """A file stripped or removed leaves the list, so the list never grows stale and silent."""
    tracked = {path.name: path for path in _tracked_results()}
    for name in BEFORE_THE_RULE:
        assert name in tracked, f"{name} is no longer tracked; drop it from BEFORE_THE_RULE"
        assert '"prediction"' in tracked[name].read_text(), f"{name} carries none; drop it"


def test_a_granted_host_generation_run_is_ignored() -> None:
    for name in ("rq1-partition-qt-p4-n1850-rp1.0.json", "rq1-partition-chromium-p1-n1850.json"):
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", f"datasets/results/{name}"], cwd=ROOT, check=False
        )
        assert ignored.returncode == 0, f"{name} is not ignored"
