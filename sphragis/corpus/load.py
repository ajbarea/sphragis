"""Reading an organization's corpus: the one place every stage and analysis gets examples from.

A built month (`examples/`) is what the build fetched and filtered; a refined month (`refined/`)
is that month under the data audit's label rules (`sphragis.corpus.refine`). Everything that
measures or trains reads refined examples, and refuses a corpus whose refinement is missing or
was made from other examples or other rules, so a changed rule cannot be half applied.
`built_examples` exists for the few analyses whose subject is what the rules removed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sphragis.corpus.refine import RULES_VERSION


def built_dir(root: Path, org: str) -> Path:
    return Path(root) / org / "examples"


def refined_dir(root: Path, org: str) -> Path:
    return Path(root) / org / "refined"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_record(root: Path, org: str, month_file: str) -> Path:
    return refined_dir(root, org) / month_file.replace(".jsonl", ".source.json")


DERIVED = "derived.json"


def mark_derived(root: Path, org: str, *, source: str) -> None:
    """Record that a corpus was cut from refined examples, as a placebo's halves are.

    A derived corpus has no built months of its own, so its freshness is the rules version its
    source was refined under, recorded here and checked on every read.
    """
    refined_dir(root, org).mkdir(parents=True, exist_ok=True)
    (refined_dir(root, org) / DERIVED).write_text(
        json.dumps({"rules": RULES_VERSION, "source": source}, indent=2)
    )


def stale_refinements(root: Path, org: str) -> list[str]:
    """Built months whose refined file is missing, or was made from other examples or rules.

    A derived corpus (no built months, a derived record) is stale when its record names other
    rules. A directory with neither is reported as a whole, so an empty read cannot pass.
    """
    derived = refined_dir(root, org) / DERIVED
    if not built_dir(root, org).is_dir():
        if derived.exists() and json.loads(derived.read_text()).get("rules") == RULES_VERSION:
            return []
        return [f"{org}: no built months and no current derived record"]
    stale = []
    for built in sorted(built_dir(root, org).glob("*.jsonl")):
        record = source_record(root, org, built.name)
        if not record.exists():
            stale.append(built.name)
            continue
        source = json.loads(record.read_text())
        if source.get("examples_sha256") != sha256(built) or source.get("rules") != RULES_VERSION:
            stale.append(built.name)
    return stale


def _read(directory: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line)
    return rows


def refined_month_files(root: Path, org: str) -> list[Path]:
    """The refined month files, refusing a missing or out-of-date refinement."""
    stale = stale_refinements(root, org)
    if stale:
        raise SystemExit(
            f"{org}: {len(stale)} built month(s) not refined under the current rules "
            f"(first: {stale[:3]}); run `python -m sphragis.corpus refine --org {org}` first"
        )
    return sorted(refined_dir(root, org).glob("*.jsonl"))


def refined_examples(root: Path, org: str) -> list[dict[str, Any]]:
    """An organization's refined examples, refusing a missing or out-of-date refinement."""
    rows: list[dict[str, Any]] = []
    for path in refined_month_files(root, org):
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line)
    return rows


def built_examples(root: Path, org: str) -> list[dict[str, Any]]:
    """Examples as built, before the label rules: only for analysing what the rules remove."""
    return _read(built_dir(root, org))
