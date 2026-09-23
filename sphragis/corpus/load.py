"""Reading an organization's corpus: the one place every stage and analysis gets examples from.

A built month (`examples/`) is what the build fetched and filtered; a refined month (`refined/`)
is that month under the data audit's label rules (`sphragis.corpus.refine`). Everything that
measures or trains reads refined examples, and refuses a corpus whose refinement is missing or
no longer matches what it was made from: each refined month carries a record of the built month,
the raw snapshots and the rules it was made from, and its own content hash, and a month is read
only while all four still match. `built_examples` exists for the few analyses whose subject is
what the rules removed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from sphragis.corpus.refine import RULES_VERSION

DERIVED = "derived.json"


def built_dir(root: Path, org: str) -> Path:
    return Path(root) / org / "examples"


def refined_dir(root: Path, org: str) -> Path:
    return Path(root) / org / "refined"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_digest(root: Path, org: str) -> str:
    """One digest over every raw snapshot of an organization: the kinds refine reads."""
    digest = hashlib.sha256()
    for path in sorted((Path(root) / org / "raw").glob("*.ndjson.gz")):
        digest.update(path.name.encode() + b"\0" + sha256(path).encode())
    return digest.hexdigest()


def source_record(root: Path, org: str, month_file: str) -> Path:
    return refined_dir(root, org) / month_file.replace(".jsonl", ".source.json")


def write_source_record(root: Path, org: str, built: Path, refined: Path) -> None:
    source_record(root, org, built.name).write_text(
        json.dumps(
            {
                "examples_sha256": sha256(built),
                "refined_sha256": sha256(refined),
                "raw_digest": raw_digest(root, org),
                "rules": RULES_VERSION,
            },
            indent=2,
        )
    )


def mark_derived(root: Path, org: str, *, source_root: Path, source_org: str) -> None:
    """Record that a corpus was cut from another's refined months, as a placebo's halves are.

    The record holds this corpus's own month hashes and its source's, so an edit to either, or a
    re-refinement of the source, makes it stale.
    """
    out = refined_dir(root, org)
    out.mkdir(parents=True, exist_ok=True)
    (out / DERIVED).write_text(
        json.dumps(
            {
                "rules": RULES_VERSION,
                "source_root": str(source_root),
                "source_org": source_org,
                "months": {p.name: sha256(p) for p in sorted(out.glob("*.jsonl"))},
                "source_months": {
                    p.name: sha256(p)
                    for p in sorted(refined_dir(source_root, source_org).glob("*.jsonl"))
                },
            },
            indent=2,
        )
    )


def _stale_derived(root: Path, org: str) -> list[str]:
    out = refined_dir(root, org)
    record_path = out / DERIVED
    if not record_path.exists():
        return [f"{org}: no built months and no derived record"]
    record = json.loads(record_path.read_text())
    if record.get("rules") != RULES_VERSION:
        return [f"{org}: derived under other rules"]
    here = {p.name: sha256(p) for p in sorted(out.glob("*.jsonl"))}
    if here != record.get("months"):
        return [f"{org}: derived months differ from their record"]
    source = refined_dir(Path(record["source_root"]), record["source_org"])
    # A derived corpus copied away from its source (to a cluster) is checked against its own
    # record alone; next to its source, the source must not have moved either.
    if source.is_dir():
        now = {p.name: sha256(p) for p in sorted(source.glob("*.jsonl"))}
        if now != record.get("source_months"):
            return [f"{org}: its source {record['source_org']} was re-refined since it was cut"]
    return []


def stale_refinements(root: Path, org: str) -> list[str]:
    """What stops this corpus being read: a reason per stale month, empty when current."""
    if not built_dir(root, org).is_dir():
        return _stale_derived(root, org)
    raw = raw_digest(root, org)
    stale = []
    built_months = sorted(built_dir(root, org).glob("*.jsonl"))
    for built in built_months:
        record_path = source_record(root, org, built.name)
        refined = refined_dir(root, org) / built.name
        if not record_path.exists() or not refined.exists():
            stale.append(f"{built.name}: not refined")
            continue
        record = json.loads(record_path.read_text())
        if record.get("rules") != RULES_VERSION:
            stale.append(f"{built.name}: refined under other rules")
        elif record.get("examples_sha256") != sha256(built):
            stale.append(f"{built.name}: rebuilt since it was refined")
        elif record.get("raw_digest") != raw:
            stale.append(f"{built.name}: raw snapshots changed since it was refined")
        elif record.get("refined_sha256") != sha256(refined):
            stale.append(f"{built.name}: refined file changed since it was written")
    names = {p.name for p in built_months}
    for orphan in sorted(refined_dir(root, org).glob("*.jsonl")):
        if orphan.name not in names:
            stale.append(f"{orphan.name}: refined with no built month")
    return stale


def refined_month_files(root: Path, org: str) -> list[Path]:
    """The refined month files, refusing a refinement that no longer matches its sources."""
    stale = stale_refinements(root, org)
    if stale:
        raise SystemExit(
            f"{org}: corpus not refined under the current rules and sources "
            f"({len(stale)}: {stale[:3]}); run `python -m sphragis.corpus refine --org {org}`"
        )
    return sorted(refined_dir(root, org).glob("*.jsonl"))


def refined_examples(root: Path, org: str) -> list[dict[str, Any]]:
    """An organization's refined examples, refusing a stale refinement."""
    rows: list[dict[str, Any]] = []
    for path in refined_month_files(root, org):
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line)
    return rows


def file_record(path: Path) -> Path:
    return Path(path).with_suffix(".source.json")


def write_derived_file(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write a corpus file cut from refined examples, with a record of the rules it was cut under.

    Files handed to a job by path (a project's examples, a client's) carry no directory for
    `stale_refinements` to check, so each carries its own record instead.
    """
    path = Path(path)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    file_record(path).write_text(
        json.dumps({"rules": RULES_VERSION, "sha256": sha256(path)}, indent=2)
    )


def stale_derived_file(path: Path) -> str | None:
    """Why a derived corpus file cannot be read, or None when it and its record still match."""
    record_path = file_record(path)
    if not record_path.exists():
        return f"{path}: no record; cut it from refined examples (scripts/project_corpora.py)"
    record = json.loads(record_path.read_text())
    if record.get("rules") != RULES_VERSION:
        return f"{path}: cut under other rules"
    if record.get("sha256") != sha256(Path(path)):
        return f"{path}: changed since it was cut"
    return None


def derived_file_rows(path: Path, *, legacy: bool = False) -> list[dict[str, Any]]:
    """A derived corpus file's examples, refusing one not cut under the current rules.

    `legacy` reads it regardless, to reproduce a result made before the rules; a caller that
    passes it records that it did.
    """
    if not legacy and (reason := stale_derived_file(path)):
        raise SystemExit(reason)
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def built_examples(root: Path, org: str) -> list[dict[str, Any]]:
    """Examples as built, before the label rules: only for analysing what the rules remove."""
    rows: list[dict[str, Any]] = []
    for path in sorted(built_dir(root, org).glob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line)
    return rows
