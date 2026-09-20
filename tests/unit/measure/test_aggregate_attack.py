"""The aggregate attack's project-level permutation, run end to end on planted updates.

These replaced three tests that searched the script's text. A review mutated the script -- `>=`
to `>`, the observed statistic read from another run, the truth check disabled -- and all three
still passed, and the one that read result files passed vacuously when run from another
directory. These run the script and read what it computes.
"""

from __future__ import annotations

import json
import math
import random
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "aggregate_attack.py"
_OWNER = {**{f"a{i}": "aosp" for i in range(3)}, **{f"q{i}": "qt" for i in range(6)}}


def _write_inputs(tmp_path: Path, strength: dict[str, float]) -> tuple[Path, Path]:
    """Three clients a project; each organization's clients share its direction by `strength`."""
    rng = random.Random(0)
    dim = 48
    directions = {org: [rng.gauss(0, 1) for _ in range(dim)] for org in strength}
    names, vectors, clients = [], [], {}
    for project, org in sorted(_OWNER.items()):
        for index in range(3):
            name = f"{project}-c{index}"
            names.append(f"run/{name}")
            vectors.append([rng.gauss(0, 1) + strength[org] * d for d in directions[org]])
            clients[name] = {"source": f"{org}:cpp/{project}"}
    norms = [math.sqrt(sum(x * x for x in v)) for v in vectors]
    cosine = [
        [
            sum(a * b for a, b in zip(u, v, strict=True)) / (norms[i] * norms[j])
            for j, v in enumerate(vectors)
        ]
        for i, u in enumerate(vectors)
    ]
    geometry = tmp_path / "geometry.json"
    geometry.write_text(
        json.dumps(
            {
                "adapters": names,
                "cosine": cosine,
                "update_norm": dict(zip(names, norms, strict=True)),
            }
        )
    )
    updates = tmp_path / "clients.json"
    updates.write_text(json.dumps({"clients": clients}))
    return geometry, updates


def _attack(tmp_path: Path, strength: dict[str, float]) -> dict:
    geometry, updates = _write_inputs(tmp_path, strength)
    out = tmp_path / "attack.json"
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--geometry",
            str(geometry),
            "--clients",
            str(updates),
            "--content",
            "cpp",
            "--beyond-project",
            "--sizes",
            "4",
            "--draws",
            "240",
            "--permutation-seeds",
            "0",
            "1",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        cwd=_ROOT,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(out.read_text())


def test_a_planted_organization_sits_at_the_floor_on_every_seed(tmp_path: Path) -> None:
    report = _attack(tmp_path, {"aosp": 3.0, "qt": 0.0})
    aosp = report["project_permutation"]["aosp"]
    assert aosp["groupings"] == math.comb(9, 3)
    assert [entry["p"] for entry in aosp["per_seed"]] == [1 / 84, 1 / 84]


def test_no_reported_p_falls_below_its_floor(tmp_path: Path) -> None:
    report = _attack(tmp_path, {"aosp": 0.0, "qt": 0.0})
    for label, result in report["project_permutation"].items():
        assert result["p_min"] >= result["floor"], label
        assert all(entry["hits"] >= 1 for entry in result["per_seed"]), "the truth counts itself"


def test_every_committed_permutation_respects_its_floor() -> None:
    """Anchored at the repository, so it cannot pass by finding no files from another directory."""
    files = sorted((_ROOT / "datasets" / "results").glob("aggregate-attack-*.json"))
    checked = 0
    for path in files:
        for label, cell in json.loads(path.read_text()).get("project_permutation", {}).items():
            if "per_seed" in cell:
                for entry in cell["per_seed"]:
                    assert entry["p"] >= cell["floor"] - 1e-12, f"{path.name}:{label}"
            elif cell.get("p") is not None:
                assert cell["p"] >= 1.0 / cell["relabelings"] - 1e-12, f"{path.name}:{label}"
            checked += 1
    assert checked, "the committed results carry at least one permutation"
