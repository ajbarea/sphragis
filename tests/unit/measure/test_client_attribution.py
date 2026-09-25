"""client_attribution.py's own wiring: run for real, on the smallest input that exercises it."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "client_attribution.py"


def _minimal_inputs(names: list[str], *, withheld: str | None) -> tuple[dict, dict]:
    """Two clients, two organizations sharing one content type: enough for every altitude."""
    dim = 8
    vectors = {"a-c0": [1.0] * dim, "b-c0": [0.0, 1.0] + [0.0] * (dim - 2)}
    norms = {n: math.sqrt(sum(x * x for x in v)) for n, v in vectors.items()}
    bases = list(vectors)
    cosine = [
        [
            sum(x * y for x, y in zip(vectors[i], vectors[j], strict=True)) / (norms[i] * norms[j])
            for j in bases
        ]
        for i in bases
    ]
    geometry = {"adapters": names, "cosine": cosine}
    report: dict = {"clients": {"a-c0": {"source": "aosp:cpp/x"}, "b-c0": {"source": "qt:cpp/y"}}}
    if withheld:
        report["withheld"] = withheld
        report["local_equals"] = {"equals": "sync_upload", "round": 5}
    return geometry, report


def _run(tmp_path: Path, names: list[str], *, withheld: str | None) -> dict:
    geometry, report = _minimal_inputs(names, withheld=withheld)
    geometry_path, clients_path, out = (
        tmp_path / "geometry.json",
        tmp_path / "clients.json",
        tmp_path / "attribution.json",
    )
    geometry_path.write_text(json.dumps(geometry))
    clients_path.write_text(json.dumps(report))
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--geometry",
            str(geometry_path),
            "--clients",
            str(clients_path),
            "--draws",
            "10",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        cwd=_ROOT,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(out.read_text())


def test_local_equals_is_carried_through_when_the_geometry_reads_the_withheld_half(
    tmp_path: Path,
) -> None:
    report = _run(tmp_path, ["run/a-c0-local", "run/b-c0-local"], withheld="local")
    assert report["local_equals"] == {"equals": "sync_upload", "round": 5}


def test_local_equals_is_absent_when_the_geometry_reads_the_transmitted_half(
    tmp_path: Path,
) -> None:
    report = _run(tmp_path, ["run/a-c0", "run/b-c0"], withheld="local")
    assert "local_equals" not in report


def test_local_equals_is_absent_when_the_run_recorded_none(tmp_path: Path) -> None:
    report = _run(tmp_path, ["run/a-c0", "run/b-c0"], withheld=None)
    assert "local_equals" not in report
