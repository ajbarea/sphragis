"""No tracked result carries model text that could repeat a review comment.

A generation conditioned on review comments, or a summary distilled from them, can repeat a
comment word for word, and no review text is published (`docs/protocol.md`). Published results
carry a keyed digest of each such text instead (`sphragis.measure.withhold`,
`scripts/withhold_text.py`); the full texts stay in the cluster's results root. Qt's and
Chromium's generation runs stay out of git altogether, as their written grants ask (research log,
2026-10-18 and 2026-10-19).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sphragis.measure.withhold import text_left

ROOT = Path(__file__).resolve().parents[2]


def _tracked_results() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "datasets/results"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ROOT / line for line in out.splitlines() if line.endswith(".json")]


def test_no_tracked_result_carries_withheld_text() -> None:
    offending = {
        path.name: left[:3]
        for path in _tracked_results()
        if (left := text_left(json.loads(path.read_text()), path.name))
    }
    assert not offending, f"results carrying model text; run scripts/withhold_text.py: {offending}"


def test_the_check_fires_on_a_published_run_shape() -> None:
    """The guard is only as good as its reach: each shape a run is published in trips it."""
    shapes = {
        "rq1-partition-openstack-p1-s1-n1850-rp1.0.json": {
            "results": {"base|a": [{"prediction": "x"}]}
        },
        "prompt-format-probe.json": {"raw": [{"prediction": "x"}]},
        "determinism-sym-0-gh-a-001-rp1.0.json": {
            "passes": {"bf16": {"openstack:I1:f.py:1:2": "x"}}
        },
        "rules-reviews-openstack-p10.json": {"files": {"openstack-a": {"file": "- a rule"}}},
    }
    for name, document in shapes.items():
        assert text_left(document, name), name


def test_a_granted_host_generation_run_is_ignored() -> None:
    for name in ("rq1-partition-qt-p4-n1850-rp1.0.json", "rq1-partition-chromium-p1-n1850.json"):
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", f"datasets/results/{name}"], cwd=ROOT, check=False
        )
        assert ignored.returncode == 0, f"{name} is not ignored"
