"""The NoteDb git route against the REST-built AOSP corpus: enumeration, fields and examples.

Every change NoteDb records as submitted in two months of one AOSP project, fetched over git
(`sphragis.corpus.notedb`) and built into examples by the same `build_from_change` the REST
corpus went through, then compared with that corpus three ways:

1. enumeration: which changes each route holds for the project-month, and why they differ;
2. fields: every REST change field the git route fills, on the changes both hold;
3. examples: ids, before/after hunks, context and comment texts, on the changes both hold.

The REST corpus is read only. Git objects go to `--work`, a scratch directory the caller
deletes afterwards: they carry raw identities, and only counts and ids reach the artifact.
Every network operation is appended to `<work>/ledger.jsonl`, so a rerun over the same work
directory costs no requests and the artifact reports what the whole comparison cost.

Run: set -a; . ./.env; set +a
     uv run --no-active python scripts/notedb_parity.py \
         --rest-root <checkout>/datasets/gerrit/aosp --work <scratch>/notedb-parity
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sphragis.corpus import gerrit_diff
from sphragis.corpus.build import build_from_change
from sphragis.corpus.cli import require_salt
from sphragis.corpus.examples import hunks_from_diff
from sphragis.corpus.notedb import (
    BRANCH_DEFAULT,
    GIT_HOSTS,
    NOTEDB_KEY,
    REVIEW_HOSTS,
    MergedCommit,
    Repo,
    branch_refs,
    candidates_since,
    collect,
    diff_key,
    embedded_fetchers,
    fetch_history,
    merged_commits,
    month_bounds,
    patch_set_commits,
    pseudonymise,
    tree_entries,
)
from sphragis.corpus.pacing import Pacer
from sphragis.provenance import provenance_header

RESULTS = Path("datasets/results/notedb-parity-aosp.json")
ORG = "aosp"
PROJECT = "platform/hardware/interfaces"
MONTHS = ("2024-11", "2025-01")

#: Change fields compared as stored. `revisions` is compared separately, per patch set.
FIELDS = (
    "id",
    "triplet_id",
    "project",
    "branch",
    "change_id",
    "subject",
    "status",
    "created",
    "updated",
    "submitted",
    "submission_id",
    "owner",
    "submitter",
    "current_revision",
    "current_revision_number",
    "meta_rev_id",
    "hashtags",
)
EXAMPLE_FIELDS = ("before", "after", "context_before", "context_after", "created", "project")
SHOWN = 10


def _since(start: str) -> str:
    return candidates_since(ORG, start)


def rest_rows(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Every REST snapshot row for the project, by snapshot month."""
    by_month = {}
    for path in sorted((root / "raw").glob("*.ndjson.gz")):
        with gzip.open(path, "rt") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        by_month[path.name.removesuffix(".ndjson.gz")] = [
            r for r in rows if r["project"] == PROJECT
        ]
    return by_month


def rest_examples(root: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Every REST-built example for the project, by (change id, created)."""
    by_change: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in sorted((root / "examples").glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line:
                example = json.loads(line)
                if example["project"] == PROJECT:
                    by_change[(example["change_id"], example["created"])].append(example)
    return by_change


def rest_timing(root: Path) -> dict[str, Any]:
    """Two properties of the whole AOSP REST corpus that the git route's design rests on.

    `lag`: days from a `main` change's final upload to its merge. AOSP merges the uploaded
    commit unchanged, so commit dates are upload dates, and a change uploaded more than the
    candidate slack before its merge is missed; this is what sets `SLACK_DAYS`.
    `month`: how often a merged change was last updated in a later month than it merged. The
    REST route files a change by last update and the git route by merge, so this is the
    share whose month differs between the routes.
    """
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "raw").glob("*.ndjson.gz")):
        with gzip.open(path, "rt") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    rows[row["id"]] = row

    def stamp(text: str) -> datetime:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")

    merged = [r for r in rows.values() if r.get("submitted")]
    lags = sorted(
        (
            stamp(r["submitted"]) - stamp(r["revisions"][r["current_revision"]]["created"])
        ).total_seconds()
        / 86400
        for r in merged
        if r.get("branch") == BRANCH_DEFAULT
    )
    later = sum(r["updated"][:7] > r["submitted"][:7] for r in merged)
    return {
        "merged_changes": len(merged),
        "updated_in_a_later_month": later,
        "updated_in_a_later_month_pct": round(100 * later / len(merged), 2),
        "main_changes": len(lags),
        "merged_more_than_n_days_after_final_upload": {
            str(days): sum(lag > days for lag in lags) for days in (14, 30, 60, 90, 180)
        },
    }


def ledger_totals(ledger: Path) -> dict[str, Any]:
    """Network operations and HTTP requests over the whole comparison, by purpose.

    Every figure here is read back from what `Repo._network` actually measured (the curl
    trace's request count) and wrote to the ledger; nothing here is a hand-entered or assumed
    value. An earlier version reported `http_requests_estimated` by reading an `estimated` key
    no code ever wrote to a ledger entry, so it was always 0 -- removed rather than kept as a
    figure with no source.
    """
    entries = [json.loads(line) for line in ledger.read_text().splitlines() if line]
    by_purpose: dict[str, dict[str, int]] = defaultdict(lambda: {"operations": 0, "http": 0})
    for entry in entries:
        by_purpose[entry["purpose"]]["operations"] += 1
        by_purpose[entry["purpose"]]["http"] += entry["http_requests"]
    return {
        "host": sorted({e["host"] for e in entries}),
        "operations": len(entries),
        "http_requests": sum(e["http_requests"] for e in entries),
        "first": entries[0]["at"] if entries else None,
        "last": entries[-1]["at"] if entries else None,
        "by_purpose": dict(by_purpose),
    }


def classify_enumeration(
    month: str,
    git: Mapping[int, dict[str, Any]],
    rest: Mapping[str, list[dict[str, Any]]],
    on_branch: Mapping[str, int],
) -> dict[str, Any]:
    """Which changes each route holds for one month, with a reason for every difference.

    The REST snapshot holds changes by last update; the git route by NoteDb submission time on
    one branch. Each difference is attributed to the first reason that explains it.
    """
    start, end = month_bounds(month)
    since = _since(start)
    here = {r["_number"]: r for r in rest.get(month, [])}
    elsewhere = {
        r["_number"]: other for other, rows in rest.items() if other != month for r in rows
    }
    rest_only: Counter[str] = Counter()
    rest_only_ids: dict[str, list[int]] = defaultdict(list)
    for number, row in sorted(here.items()):
        if number in git:
            continue
        stamps = [on_branch.get(sha) for sha in row.get("revisions", {})]
        landed = [stamp for stamp in stamps if stamp is not None]
        uploaded = max(str(rev.get("created", "")) for rev in row.get("revisions", {}).values())
        submitted = str(row.get("submitted") or "")
        if not start <= submitted < end:
            reason = "submitted_outside_month_updated_inside"
        elif landed:
            floor = datetime.fromisoformat(since).replace(tzinfo=UTC).timestamp()
            reason = "committed_before_candidate_slack" if max(landed) < floor else "unexplained"
        elif row.get("branch") != BRANCH_DEFAULT:
            reason = "other_branch_never_reached_main"
        elif uploaded < since:
            reason = "uploaded_before_fetched_history"
        else:
            reason = "main_commit_not_in_history"
        rest_only[reason] += 1
        rest_only_ids[reason].append(number)
    git_only: Counter[str] = Counter()
    git_only_ids: dict[str, list[int]] = defaultdict(list)
    for number in sorted(git):
        if number in here:
            continue
        reason = (
            f"in_rest_snapshot_{elsewhere[number]}"
            if number in elsewhere
            else "in_no_rest_snapshot"
        )
        git_only[reason] += 1
        git_only_ids[reason].append(number)
    return {
        "rest": len(here),
        "git": len(git),
        "both": len(set(here) & set(git)),
        "rest_only": dict(rest_only),
        "git_only": dict(git_only),
        "rest_only_changes": {k: v[:SHOWN] for k, v in rest_only_ids.items()},
        "git_only_changes": {k: v[:SHOWN] for k, v in git_only_ids.items()},
    }


def _lines_unavailable_reason(repo: Repo, git: Mapping[str, Any]) -> str:
    """Why `files` (and so `lines.insertions_deletions`) is unavailable for this change.

    Mirrors the three causes `collect` itself counts (`files_unavailable_*`): the merged
    commit has more than one parent, its single parent lies outside the fetched history, or it
    never landed on the branch read at all.
    """
    merged_commit = git.get("merged_commit")
    if not merged_commit:
        return "not_on_branch"
    parents = repo.git("log", "--no-walk", "--format=%P", merged_commit).decode().split()
    if len(parents) > 1:
        return "merge_commit"
    if parents and repo.missing(parents):
        return "parent_outside_history"
    return "unclassified"


def compare_fields(
    repo: Repo,
    pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    meta_ancestry: Mapping[int, set[str]],
) -> dict[str, Any]:
    """Field-by-field agreement on changes both routes hold."""
    agree: Counter[str] = Counter()
    differ: dict[str, list[int]] = defaultdict(list)
    meta_advanced = 0
    for git, rest in pairs:
        number = rest["_number"]
        for name in FIELDS:
            if name not in rest:
                continue
            if git.get(name) == rest[name]:
                agree[name] += 1
            elif name == "hashtags" and sorted(git.get(name) or []) == sorted(rest[name]):
                agree["hashtags_as_set"] += 1
            elif name == "meta_rev_id" and rest[name] in meta_ancestry.get(number, set()):
                meta_advanced += 1
            else:
                differ[name].append(number)
        g_rev, r_rev = git.get("revisions", {}), rest.get("revisions", {})
        if set(g_rev) != set(r_rev):
            differ["revisions.set"].append(number)
        else:
            agree["revisions.set"] += 1
        for sha in set(g_rev) & set(r_rev):
            for name in ("_number", "created", "uploader", "ref", "branch"):
                key = f"revisions.{name}"
                if g_rev[sha].get(name) == r_rev[sha].get(name):
                    agree[key] += 1
                else:
                    differ[key].append(number)
        files = git.get("files")
        if files is None:
            differ[f"lines.{_lines_unavailable_reason(repo, git)}"].append(number)
        elif "insertions" not in rest:
            differ["lines.rest_missing_insertions"].append(number)
        else:
            inserted = sum(f["lines_inserted"] or 0 for f in files.values())
            deleted = sum(f["lines_deleted"] or 0 for f in files.values())
            key = "lines.insertions_deletions"
            if (inserted, deleted) == (rest["insertions"], rest["deletions"]):
                agree[key] += 1
            else:
                differ[key].append(number)
    return {
        "changes": len(pairs),
        "agree": dict(sorted(agree.items())),
        "differ": {k: len(v) for k, v in sorted(differ.items())},
        "differ_changes": {k: v[:SHOWN] for k, v in sorted(differ.items())},
        "meta_rev_id_advanced_since_rest_fetch": meta_advanced,
    }


def compare_examples(
    pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    rest_by_change: Mapping[tuple[str, str], list[dict[str, Any]]],
    details: Path,
) -> dict[str, Any]:
    """Examples built from each route's data for the same changes, compared id by id."""
    counts: Counter[str] = Counter()
    ids: dict[str, list[str]] = defaultdict(list)
    drops: Counter[str] = Counter()
    with details.open("w") as out:
        for git, rest in pairs:
            built, dropped = build_from_change(ORG, git, *embedded_fetchers(git))
            drops.update(dropped)
            mine = {e["id"]: e for e in built}
            theirs = {
                e["id"]: e for e in rest_by_change.get((rest["change_id"], rest["created"]), [])
            }
            counts["git_examples"] += len(mine)
            counts["rest_examples"] += len(theirs)
            for key in sorted(set(mine) & set(theirs)):
                counts["same_id"] += 1
                bad = [f for f in EXAMPLE_FIELDS if mine[key].get(f) != theirs[key].get(f)]
                if mine[key]["comments"] != theirs[key]["comments"]:
                    same_set = Counter(mine[key]["comments"]) == Counter(theirs[key]["comments"])
                    bad.append("comments_order" if same_set else "comments")
                for name in bad:
                    counts[f"field_{name}"] += 1
                    ids[f"field_{name}"].append(key)
                if not bad:
                    counts["identical"] += 1
                else:
                    out.write(
                        json.dumps(
                            {"id": key, "differs": bad, "git": mine[key], "rest": theirs[key]}
                        )
                        + "\n"
                    )
            only_git = {k: mine[k] for k in set(mine) - set(theirs)}
            only_rest = {k: theirs[k] for k in set(theirs) - set(mine)}
            by_file_git = defaultdict(list)
            for key, example in only_git.items():
                by_file_git[(example["path"], example["patch_set"])].append(key)
            for key, example in sorted(only_rest.items()):
                partners = by_file_git.get((example["path"], example["patch_set"]))
                kind = "rest_only_same_file_patch_set" if partners else "rest_only"
                counts[kind] += 1
                ids[kind].append(key)
                out.write(
                    json.dumps({"id": key, "kind": kind, "rest": example, "git_partners": partners})
                    + "\n"
                )
            by_file_rest = {(e["path"], e["patch_set"]) for e in only_rest.values()}
            for key, example in sorted(only_git.items()):
                kind = (
                    "git_only_same_file_patch_set"
                    if (example["path"], example["patch_set"]) in by_file_rest
                    else "git_only"
                )
                counts[kind] += 1
                ids[kind].append(key)
                out.write(json.dumps({"id": key, "kind": kind, "git": example}) + "\n")
    return {
        "counts": dict(sorted(counts.items())),
        "ids": {k: sorted(v)[:SHOWN] for k, v in sorted(ids.items())},
        "git_drops": dict(sorted(drops.items())),
    }


def _carried(start: int, end: int, mapping: Sequence[gerrit_diff.Edit]) -> tuple[int, int]:
    """Lines [start, end) of a parent, located in its patch set by the patch set's own diff.

    A line the author also changed maps onto the author's region rather than being dropped,
    which is the one respect in which this is looser than Gerrit's placement: it answers
    whether a hunk touches upstream work at all, not whether Gerrit would attribute it.
    """
    low, shift = None, 0
    for m in mapping:
        if m.begin_a <= start < m.end_a:
            low = m.begin_b
            break
        if m.end_a <= start:
            shift = m.end_b - m.end_a
        else:
            break
    low = start + shift if low is None else low
    high, shift = None, 0
    for m in mapping:
        if m.begin_a < end <= m.end_a:
            high = m.end_b
            break
        if m.end_a <= end:
            shift = m.end_b - m.end_a
        else:
            break
    high = end + shift if high is None else high
    return low, max(low, high)


def _touches(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Whether two half-open line ranges meet, an empty range meeting what surrounds it."""
    if a[0] == a[1]:
        return b[0] <= a[0] <= b[1]
    if b[0] == b[1]:
        return a[0] <= b[0] <= a[1]
    return max(a[0], b[0]) < min(a[1], b[1])


def _block_due_to_rebase(diff: Mapping[str, Any], before_start: int) -> bool:
    """Whether the edit block whose old side starts at `before_start` is marked a rebase edit.

    `hunks_from_diff` numbers an edit block by the old-side line it starts on, counting every
    block's `ab` or `a` lines, so walking the blocks the same way finds the hunk's own.
    """
    line = 1
    for block in diff["content"]:
        if "ab" in block:
            line += len(block["ab"])
            continue
        if line == before_start:
            return bool(block.get("due_to_rebase"))
        line += len(block.get("a", []))
    return False


def measure_rebases(
    repo: Repo, pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]]
) -> dict[str, Any]:
    """Per example, whether its n -> n+1 step was a rebase and whether its hunk is upstream's.

    Built as the REST corpus was, which never reads the flag, so these are the examples it
    holds. `due_to_rebase` is Gerrit's exact attribution, and `with_rebase_edit_drop` counts
    what dropping it would remove; `overlaps_upstream` is looser (`_carried`), and REST's own
    successor `kind` is crossed with both.
    """
    counts: Counter[str] = Counter()
    by_kind: dict[str, Counter[str]] = defaultdict(Counter)
    ids: dict[str, list[str]] = defaultdict(list)
    upstream: Counter[str] = Counter()
    dropped: Counter[str] = Counter()
    for git, rest in pairs:
        for computed in git[NOTEDB_KEY]["diffs"].values():
            edits = [b for b in computed["content"] if "ab" not in b and not b.get("common")]
            upstream["diff_edit_blocks"] += len(edits)
            upstream["diff_edit_blocks_due_to_rebase"] += sum(
                bool(b.get("due_to_rebase")) for b in edits
            )
        built, _ = build_from_change(ORG, git, *embedded_fetchers(git))
        for example in built:
            diff = git[NOTEDB_KEY]["diffs"][diff_key(example["patch_set"], example["path"])]
            if _block_due_to_rebase(diff, int(example["id"].rsplit(":", 1)[1])):
                dropped["comments_dropped_rebase_edit"] += len(example["comments"])
            else:
                dropped["examples_kept_with_drop"] += 1
        by_number = {rev["_number"]: (sha, rev) for sha, rev in git["revisions"].items()}
        measured: dict[tuple[int, str], Any] = {}
        for example in built:
            ps, path, start = (
                example["patch_set"],
                example["path"],
                int(example["id"].rsplit(":", 1)[1]),
            )
            (a_sha, a_rev), (b_sha, b_rev) = by_number[ps], by_number[ps + 1]
            kind = rest["revisions"].get(b_sha, {}).get("kind", "unknown")
            counts["examples"] += 1
            by_kind[kind]["examples"] += 1
            hunk = next(
                h
                for h in hunks_from_diff(git[NOTEDB_KEY]["diffs"][diff_key(ps, path)])
                if h.before_start == start
            )
            if gerrit_diff.related(a_rev["parents"], b_rev["parents"], a_sha, b_sha):
                continue
            counts["on_rebase_step"] += 1
            by_kind[kind]["on_rebase_step"] += 1
            if (ps, path) not in measured:
                shas = [a_rev["parents"][0], b_rev["parents"][0], a_sha, b_sha]
                oids = [tree_entries(repo, sha, [path]).get(path) for sha in shas]
                blobs = repo.read_objects([o for o in oids if o])
                pa, pb, a, b = (blobs.get(o) if o else None for o in oids)
                texts = [gerrit_diff.RawText(x or b"") for x in (pa, pb, a, b)]
                parents_diff = gerrit_diff.edits(texts[0], texts[1])
                placed, lost = gerrit_diff.rebase_edits(pa, pb, a or b"", b or b"")
                upstream["file_steps"] += 1
                upstream["upstream_edits"] += len(parents_diff)
                upstream["placed"] += len(placed)
                upstream["lost_to_the_authors_edits"] += lost
                side_a = gerrit_diff.edits(texts[0], texts[2])
                side_b = gerrit_diff.edits(texts[1], texts[3])
                measured[(ps, path)] = [
                    (_carried(e.begin_a, e.end_a, side_a), _carried(e.begin_b, e.end_b, side_b))
                    for e in parents_diff
                ]
            span_a = (hunk.before_start - 1, hunk.before_start - 1 + len(hunk.before))
            span_b = (hunk.after_start - 1, hunk.after_start - 1 + len(hunk.after))
            overlaps = any(
                _touches(span_a, ra) or _touches(span_b, rb) for ra, rb in measured[(ps, path)]
            )
            due_to_rebase = _block_due_to_rebase(
                git[NOTEDB_KEY]["diffs"][diff_key(ps, path)], hunk.before_start
            )
            for name, hit in (
                ("due_to_rebase", due_to_rebase),
                ("overlaps_upstream", overlaps),
            ):
                if hit:
                    counts[name] += 1
                    by_kind[kind][name] += 1
                    ids[name].append(example["id"])
            if overlaps and not due_to_rebase:
                counts["overlaps_upstream_not_attributed"] += 1
    return {
        "counts": dict(sorted(counts.items())),
        "by_successor_kind": {k: dict(sorted(v.items())) for k, v in sorted(by_kind.items())},
        "upstream_edits": dict(upstream),
        "with_rebase_edit_drop": dict(dropped),
        "ids": {k: sorted(v)[:SHOWN] for k, v in sorted(ids.items())},
    }


def _relative_to_repo(path: Path) -> str:
    """`path` relative to the repository root, or its resolved form outside the repository."""
    resolved = path.resolve()
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
    )
    if top.returncode == 0:
        try:
            return str(resolved.relative_to(top.stdout.strip()))
        except ValueError:
            pass
    return str(resolved)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rest-root", type=Path, default=Path("datasets/gerrit/aosp"))
    parser.add_argument(
        "--work", type=Path, required=True, help="scratch directory for git objects"
    )
    parser.add_argument("--out", type=Path, default=RESULTS)
    parser.add_argument(
        "--branch",
        action="append",
        default=[],
        help="restrict enumeration to this branch (repeatable); default matches the existing "
        f"comparison, {BRANCH_DEFAULT} only, not every branch (item 6's 'all')",
    )
    args = parser.parse_args()
    salt = require_salt()
    args.work.mkdir(parents=True, exist_ok=True)
    ledger = args.work / "ledger.jsonl"
    branches = args.branch or [BRANCH_DEFAULT]

    url = f"{GIT_HOSTS[ORG]}/{PROJECT}"
    repo = Repo.open(args.work / "hardware-interfaces.git", url, Pacer(1.0), ledger=ledger)
    bounds = {month: month_bounds(month) for month in MONTHS}
    earliest = min(_since(start) for start, _ in bounds.values())
    branch_ref_names = branch_refs(repo, ORG, branches)
    # The history is fetched once per start date; an earlier start deepens it.
    marker = args.work / "history-since.txt"
    held = marker.read_text().strip() if marker.is_file() else None
    if held is None or earliest < held:
        fetch_history(repo, branch_ref_names, earliest)
        marker.write_text(earliest + "\n")
    listing = args.work / "refs-changes.tsv"
    if not listing.is_file():
        pairs = repo.list_remote("patch_set_refs", "refs/changes/*")
        listing.write_text("".join(f"{oid}\t{ref}\n" for oid, ref in pairs))
    change_refs = [tuple(line.split("\t")) for line in listing.read_text().splitlines() if line]
    by_commit = patch_set_commits(repo, change_refs)

    on_branch = {
        line.split()[0]: int(line.split()[1])
        for line in repo.git("log", "--format=%H %ct", *branch_ref_names).decode().splitlines()
    }
    candidates: dict[int, MergedCommit] = {}
    enumeration_counts = {}
    for month, (start, end) in bounds.items():
        found, counts = merged_commits(
            repo,
            branch_ref_names,
            _since(start),
            end,
            review_host=REVIEW_HOSTS[ORG],
            project=PROJECT,
            by_commit=by_commit,
        )
        enumeration_counts[month] = counts
        candidates.update({m.number: m for m in found if m.number is not None})

    rows, collect_counts = collect(repo, sorted(candidates), project=PROJECT, merged=candidates)
    rest = rest_rows(args.rest_root)
    rest_by_number = {r["_number"]: r for rows_ in rest.values() for r in rows_}
    meta_ancestry = {
        row["_number"]: set(repo.git("rev-list", row["meta_rev_id"]).decode().split())
        for row in rows
    }

    enumeration: dict[str, Any] = {}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for month, (start, end) in bounds.items():
        in_month = {
            row["_number"]: pseudonymise(row, salt)
            for row in rows
            if row["status"] == "MERGED" and start <= str(row.get("submitted", "")) < end
        }
        enumeration[month] = {
            "candidate_commits": enumeration_counts[month],
            **classify_enumeration(month, in_month, rest, on_branch),
        }
        pairs += [
            (row, rest_by_number[n])
            for n, row in sorted(in_month.items())
            if n in {r["_number"] for r in rest[month]}
        ]

    result = {
        **provenance_header(),
        "org": ORG,
        "project": PROJECT,
        "branches": branches,
        "months": list(MONTHS),
        "rest_root": _relative_to_repo(args.rest_root),
        "requests": ledger_totals(ledger),
        "collect": collect_counts,
        "enumeration": enumeration,
        "fields": compare_fields(repo, pairs, meta_ancestry),
        "rest_timing": rest_timing(args.rest_root),
        "rebase": measure_rebases(repo, pairs),
        "examples": compare_examples(
            pairs, rest_examples(args.rest_root), args.work / "example-diffs.jsonl"
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps({k: result[k] for k in ("requests", "enumeration", "fields")}, indent=1)[:6000]
    )
    print(json.dumps(result["examples"]["counts"], indent=1))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
