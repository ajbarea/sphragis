"""Reading an organization's corpus: the one place every stage and analysis gets examples from.

A built month (`examples/`) is what the build fetched and filtered; a refined month (`refined/`)
is that month under the data audit's label rules (`sphragis.corpus.refine`). Everything that
measures or trains reads refined examples, and refuses a corpus whose refinement is missing or
no longer matches what it was made from: each built month records the build rules it was built
under, each refined month the built month, raw snapshots and label rules it was made from and
its own content hash, and a month is read only while all of them still match. `built_examples`
exists for the few analyses whose subject is what the rules removed.

Corpora cut from refined months (a placebo's halves, a project's or a client's examples) record
their sources the same way, and are refused once a source has moved or gone stale.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from sphragis.corpus.rules import BUILD_RULES, RULES_VERSION

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


def build_record(built: Path) -> Path:
    """Where the build writes what a month was built from and under which rules."""
    return built.with_name(built.name.removesuffix(".jsonl") + ".source.json")


def write_build_record(built: Path, snapshot_sha256: str | None, *, complete: bool) -> None:
    """What a month was built from and under which build rules; `complete` once it has landed."""
    record = {"snapshot_sha256": snapshot_sha256, "build_rules": BUILD_RULES, "complete": complete}
    build_record(built).write_text(json.dumps(record, indent=2) + "\n")


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


def _months(root: Path, org: str) -> dict[str, str]:
    return {p.name: sha256(p) for p in sorted(refined_dir(root, org).glob("*.jsonl"))}


def _source(root: Path, org: str) -> dict[str, Any]:
    """What a derived corpus records of one source: where it is and what its months were."""
    return {"root": str(Path(root).resolve()), "org": org, "months": _months(root, org)}


def _stale_source(source: Mapping[str, Any]) -> str | None:
    """Why a recorded source no longer backs what was cut from it, or None.

    A derived corpus copied away from its source (to a cluster) is checked against its own
    record alone; next to its source, the source must be current and must not have moved.
    """
    root, org = Path(source["root"]), source["org"]
    if not refined_dir(root, org).is_dir():
        return None
    if stale_refinements(root, org):
        return f"its source {org} is itself stale"
    if _months(root, org) != source.get("months"):
        return f"its source {org} was re-refined since it was cut"
    return None


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
                "months": _months(root, org),
                "source": _source(source_root, source_org),
            },
            indent=2,
        )
    )


def _stale_derived(root: Path, org: str) -> list[str]:
    record_path = refined_dir(root, org) / DERIVED
    if not record_path.exists():
        return [f"{org}: no built months and no derived record"]
    record = json.loads(record_path.read_text())
    if record.get("rules") != RULES_VERSION:
        return [f"{org}: derived under other rules"]
    months = _months(root, org)
    if not months:
        return [f"{org}: derived record but no months"]
    if months != record.get("months"):
        return [f"{org}: derived months differ from their record"]
    if "source" not in record:
        return [f"{org}: derived record names no source"]
    reason = _stale_source(record["source"])
    return [f"{org}: {reason}"] if reason else []


def _stale_build(built: Path) -> str | None:
    record_path = build_record(built)
    if not record_path.is_file():
        return f"{built.name}: no build record"
    record = json.loads(record_path.read_text())
    # Records written before the completion marker carry no `complete` key and were closed.
    if not record.get("complete", True):
        return f"{built.name}: build did not complete"
    if record.get("build_rules") != BUILD_RULES:
        return f"{built.name}: built under other build rules; rebuild it"
    snapshot = built.parent.parent / "raw" / built.name.replace(".jsonl", ".ndjson.gz")
    if snapshot.is_file() and record.get("snapshot_sha256") != sha256(snapshot):
        return f"{built.name}: built from a snapshot since refetched"
    return None


def stale_refinements(root: Path, org: str) -> list[str]:
    """What stops this corpus being read: a reason per stale month, empty when current."""
    if not built_dir(root, org).is_dir():
        return _stale_derived(root, org)
    built_months = sorted(built_dir(root, org).glob("*.jsonl"))
    if not built_months:
        return [f"{org}: no built months"]
    raw = raw_digest(root, org)
    stale = []
    for built in built_months:
        if reason := _stale_build(built):
            stale.append(reason)
            continue
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
            f"{org}: corpus not current under the build and label rules "
            f"({len(stale)}: {stale[:3]}); rebuild or refine it (`python -m sphragis.corpus`)"
        )
    return sorted(refined_dir(root, org).glob("*.jsonl"))


def refined_examples(root: Path, org: str) -> list[dict[str, Any]]:
    """An organization's refined examples, refusing a stale refinement."""
    rows: list[dict[str, Any]] = []
    for path in refined_month_files(root, org):
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line)
    return rows


def file_record(path: Path) -> Path:
    return Path(path).with_name(Path(path).name + ".source.json")


def write_derived_file(
    path: Path, rows: Iterable[Mapping[str, Any]], *, sources: Sequence[tuple[Path, str]]
) -> None:
    """Write a corpus file cut from refined examples, with a record of its rules and sources.

    Files handed to a job by path (a project's examples, a client's) carry no directory for
    `stale_refinements` to check, so each carries its own record instead.
    """
    path = Path(path)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    file_record(path).write_text(
        json.dumps(
            {
                "rules": RULES_VERSION,
                "sha256": sha256(path),
                "sources": [_source(root, org) for root, org in sources],
            },
            indent=2,
        )
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
    if not record.get("sources"):
        return f"{path}: its record names no source"
    for source in record["sources"]:
        if reason := _stale_source(source):
            return f"{path}: {reason}"
    return None


def derived_sources(path: Path) -> list[tuple[Path, str]]:
    """The sources a derived file was cut from, for a file cut from it in turn."""
    record = json.loads(file_record(path).read_text())
    return [(Path(source["root"]), source["org"]) for source in record["sources"]]


def derived_file_rows(path: Path, *, legacy: bool = False) -> list[dict[str, Any]]:
    """A derived corpus file's examples, refusing one not cut under the current rules.

    `legacy` reads it regardless, to reproduce a result made before the rules; a caller that
    passes it records that it did.
    """
    if not legacy and (reason := stale_derived_file(path)):
        raise SystemExit(reason)
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
    if not rows:
        raise SystemExit(f"{path}: holds no examples")
    return rows


def built_examples(root: Path, org: str) -> list[dict[str, Any]]:
    """Examples as built, before the label rules: only for analysing what the rules remove."""
    rows: list[dict[str, Any]] = []
    for path in sorted(built_dir(root, org).glob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line)
    return rows
