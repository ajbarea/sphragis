"""The NoteDb git route against the REST-built AOSP corpus: enumeration, fields and examples.

Every change NoteDb records as submitted in two months of one AOSP project, fetched over git
(`sphragis.corpus.notedb`) and built into examples by the same `build_from_change` the REST
corpus went through, then compared with that corpus three ways:

1. enumeration: which changes each route holds for the project-month, and why they differ;
2. fields: every REST change field the git route fills, on the changes both hold;
3. examples: ids, before/after hunks, context and comment texts, on the changes both hold.

The REST corpus is read only. Git objects go to a scratch directory this script creates fresh
under `--work` (never `--work` itself, which is never touched beyond that) and deletes when the
run finishes -- on an error or a signal too -- because it carries raw identities and only counts
and ids reach the artifact; pass `--keep-work` to keep it instead. A directory is only ever
deleted if this script marked it as its own scratch on creation, so a pre-existing `--work` and
anything else in it are left alone whatever happens mid-run. Every network operation is appended
to `<scratch>/ledger.jsonl`, so the artifact reports what the whole comparison cost. A rerun over
a kept scratch directory does *not* cost no requests: reading which branches to enumerate
(`branch_refs`) runs one `ls-remote` every time regardless, and the sealed test window's
meta-tip probe is re-forced every time by design (item 1); what a kept directory actually saves
is not refetching blobs, trees and patch-set commits already held.

Before the scratch directory (and its ledger) is deleted, whether the run succeeded or raised,
the ledger is copied out to `<work>/ledger-<timestamp>.jsonl` and its path printed -- otherwise a
failed run's own record of what it asked the server for is lost along with the scratch directory
that carried it. The ledger itself holds only operation counts, purposes and timings, never a
raw identity.

Run: set -a; . ./.env; set +a
     uv run --no-active python scripts/notedb_parity.py \
         --rest-root <checkout>/datasets/gerrit/aosp --work <scratch>/notedb-parity --keep-work
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import signal
import subprocess
import tempfile
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
    _raise_on_sigterm,
    _uninterruptible,
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
from sphragis.corpus.refine import ChangeIndex, index_changes, refine
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


def rest_examples(
    root: Path, stage: str = "examples"
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Every REST example for the project at `stage` (`examples` as built, or `refined`), by
    (change id, created)."""
    by_change: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in sorted((root / stage).glob("*.jsonl")):
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
    branches_read: Sequence[str],
) -> dict[str, Any]:
    """Which changes each route holds for one month, with a reason for every difference.

    The REST snapshot holds changes by last update; the git route by NoteDb submission time on
    the branches this run actually read. Each difference is attributed to the first reason that
    explains it. `branches_read` is `branch_refs`'s own result -- the branches this run actually
    asked NoteDb for, not `--branch` as requested, which can name a branch the project has none
    of -- so a REST-only change on a branch outside that set is `branch_not_read`, whatever was
    requested; a run restricted to fewer branches than the comparison's usual default sees more
    of these, not a different label for the same cause.
    """
    start, end = month_bounds(month)
    since = _since(start)
    here = {r["_number"]: r for r in rest.get(month, [])}
    elsewhere = {
        r["_number"]: other for other, rows in rest.items() if other != month for r in rows
    }
    read_branch_names = {ref.rsplit("/", 1)[-1] for ref in branches_read}
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
        elif row.get("branch") not in read_branch_names:
            reason = "branch_not_read"
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
            for name in ("_number", "created", "uploader", "ref", "branch", "kind"):
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
    refine_index: ChangeIndex | None = None,
) -> dict[str, Any]:
    """Examples built from each route's data for the same changes, compared id by id.

    With `refine_index`, the git route's examples go through `refine` first, for comparison with
    the REST corpus as refined: the corpus that trains. The REST examples as built were stamped
    once under the current build rules, so they keep what the build now drops and `refine`
    removes (Gerrit's one-click "Acknowledged").
    """
    counts: Counter[str] = Counter()
    ids: dict[str, list[str]] = defaultdict(list)
    drops: Counter[str] = Counter()
    with details.open("w") as out:
        for git, rest in pairs:
            built, dropped = build_from_change(ORG, git, *embedded_fetchers(git))
            drops.update(dropped)
            if refine_index is not None:
                built, _ = refine(built, refine_index)
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
    """`path` relative to the git top-level of the checkout that *contains it*, not this
    process's own cwd: `--rest-root` can be a path in another checkout, or in a worktree of
    this one, whose top-level a plain `git rev-parse` run from cwd would get wrong -- or, run
    from outside any checkout at all, would misreport as whatever repository cwd happens to sit
    in. Never an absolute path in the artifact, which would otherwise carry `/home/<user>/...`.
    """
    resolved = path.resolve()
    top = subprocess.run(
        ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top.returncode == 0:
        try:
            return str(resolved.relative_to(top.stdout.strip()))
        except ValueError:
            pass
    return str(resolved)


def collect_span(bounds: Mapping[str, tuple[str, str]], months: Sequence[str]) -> tuple[str, str]:
    """One contiguous `submitted_between` span covering every month in `months`.

    `collect`'s own `submitted_between` drops a change before its patch sets, diffs or blobs
    are fetched -- not merely before it reaches a row -- so calling `collect` with none, as this
    script once did over the whole `candidates` union, fetched a full meta chain and notes for
    every candidate commit-dated near any compared month, however far its actual NoteDb
    submission time landed. `months` need not be adjacent (there can be a gap, as between the
    two months this comparison uses), so a change submitted in a gap month still reaches
    `collect` unfiltered by this alone -- `submitted_between` takes one contiguous range, not a
    set of months -- but everything outside the earliest start and the latest end is cut.
    """
    starts = [bounds[month][0] for month in months]
    ends = [bounds[month][1] for month in months]
    return min(starts), max(ends)


def _history_fetched(marker: Path, branches: Sequence[str], since: str) -> dict[str, Any] | None:
    """The branch set and `since` date `fetch_history` was last run for, or None if it must run.

    None when there is no marker, when it does not parse, when a branch this run reads is not
    among those already fetched (a rerun asking for more branches than before must still fetch
    the ones it has not seen), or when `since` is earlier than what was fetched (deepening the
    history already held).
    """
    if not marker.is_file():
        return None
    try:
        held = json.loads(marker.read_text())
    except ValueError:
        return None
    if not isinstance(held, dict) or "branches" not in held or "since" not in held:
        return None
    if not set(branches) <= set(held["branches"]):
        return None
    if since < held["since"]:
        return None
    return held


def _record_history_fetch(
    marker: Path, branches: Sequence[str], since: str, walked: Sequence[str] | None = None
) -> None:
    """Record the union of branches fetched so far, the earliest `since` reached, and which of
    the requested branches actually turned out non-dormant (`walked`, `fetch_history`'s own
    result -- defaults to `branches` when the caller has none, i.e. every requested branch was
    walked). A branch once recorded walked stays walked: `since` only ever deepens (moves
    earlier) between calls the marker accepts, and a branch whose tip cleared an earlier,
    later `since` clears any earlier one too.
    """
    held: dict[str, Any] = {}
    if marker.is_file():
        try:
            held = json.loads(marker.read_text())
        except ValueError:
            held = {}
    branches_held = set(branches) | set(held.get("branches", []))
    since_held = min(since, held.get("since", since))
    walked_held = set(walked if walked is not None else branches) | set(held.get("walked", []))
    marker.write_text(
        json.dumps(
            {
                "branches": sorted(branches_held),
                "since": since_held,
                "walked": sorted(walked_held),
            }
        )
        + "\n"
    )


#: Name of the marker file `_make_scratch` writes into every scratch directory it creates.
#: `_rmtree_marked` refuses to delete anything lacking it, so cleanup can never reach a
#: directory this script did not itself create -- including `--work` as named, which is never
#: deleted, only ever a directory made fresh underneath it.
_SCRATCH_MARKER = ".sphragis-notedb-parity-scratch"


def _checkout_root() -> Path:
    """The git checkout `scripts/notedb_parity.py` itself is running from."""
    here = Path(__file__).resolve().parent
    top = subprocess.run(
        ["git", "-C", str(here), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top.returncode == 0:
        return Path(top.stdout.strip()).resolve()
    return here.parent


def _is_or_ancestor_of(candidate: Path, other: Path) -> bool:
    """True if `candidate` equals `other`, or `other` sits somewhere inside `candidate`."""
    try:
        other.relative_to(candidate)
    except ValueError:
        return False
    return True


def _refuse_unsafe_work(work: Path, rest_root: Path, out: Path) -> None:
    """Refuse before any work if `--work` is, or contains, somewhere this script must not touch.

    Cleanup only ever deletes a directory this script creates fresh under `--work` -- never
    `--work` as named -- but that is no protection if `--work` (or an ancestor of it) *is* one
    of these paths: everything under it, marker or not, is then somewhere this script has no
    business creating scratch in. Every path is resolved (symlinks followed) first, so a
    symlink cannot disguise one of these locations as somewhere safe.
    """
    work_r = work.resolve()
    targets = {
        "the repository checkout this script runs from": _checkout_root(),
        "--rest-root": rest_root.resolve(),
        "the --out directory": out.resolve().parent,
    }
    for label, target in targets.items():
        if _is_or_ancestor_of(work_r, target):
            raise SystemExit(
                f"--work {work} is, or contains, {label} ({target}); refusing before doing any "
                "work, since a directory under --work is deleted when this run finishes"
            )


def _find_marked_scratch(work: Path) -> Path | None:
    """An existing scratch directory under `work` this script made on an earlier, kept run."""
    if not work.is_dir():
        return None
    for child in sorted(work.iterdir()):
        if not child.is_symlink() and child.is_dir() and (child / _SCRATCH_MARKER).is_file():
            return child
    return None


def _make_scratch(work: Path) -> Path:
    """A fresh, empty scratch directory under `work`, marked so only it is ever deleted."""
    work.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="notedb-parity-", dir=str(work)))
    (scratch / _SCRATCH_MARKER).write_text("")
    return scratch


def _rmtree_marked(scratch: Path) -> None:
    """Delete `scratch`, refusing unless it is a real, marked directory this script created."""
    if scratch.is_symlink() or not scratch.is_dir() or not (scratch / _SCRATCH_MARKER).is_file():
        raise RuntimeError(
            f"refusing to delete {scratch}: not a directory this script marked as its own scratch"
        )
    shutil.rmtree(scratch, ignore_errors=True)


def _preserve_ledger(scratch: Path, work: Path) -> Path | None:
    """Copy the scratch ledger out to `work` before `scratch` (and the ledger with it) is
    deleted, so a failed run's record of the network requests it made is not lost with it.

    Named with a microsecond UTC timestamp and never overwritten: an existing path at that
    name gets a numeric suffix instead, so two runs landing in the same microsecond -- or a
    kept scratch directory reused across runs -- each get their own file. Returns None, writing
    nothing, when this run made no ledger at all (a failure before `Repo.open`).
    """
    ledger = scratch / "ledger.jsonl"
    if not ledger.is_file():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%f")
    dest = work / f"ledger-{stamp}.jsonl"
    suffix = 1
    while dest.exists():
        dest = work / f"ledger-{stamp}-{suffix}.jsonl"
        suffix += 1
    shutil.copyfile(ledger, dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rest-root", type=Path, default=Path("datasets/gerrit/aosp"))
    parser.add_argument(
        "--work", type=Path, required=True, help="directory to hold this run's scratch git objects"
    )
    parser.add_argument("--out", type=Path, default=RESULTS)
    parser.add_argument(
        "--branch",
        action="append",
        default=[],
        help="restrict enumeration to this branch (repeatable); default is every branch Gerrit "
        "accepts changes on, the same default the CLI's --via git uses",
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="keep --work after this run instead of deleting it; a rerun then reuses the "
        "fetched git objects (see fetch_month for what that still costs)",
    )
    args = parser.parse_args()
    salt = require_salt()
    if not (args.rest_root / "raw").is_dir():
        raise SystemExit(
            f"--rest-root {args.rest_root} holds no raw/ REST snapshots; nothing to compare"
        )
    _refuse_unsafe_work(args.work, args.rest_root, args.out)
    scratch = _find_marked_scratch(args.work) or _make_scratch(args.work)
    try:
        with _raise_on_sigterm():
            _run(args, salt, scratch)
    finally:
        if not args.keep_work:
            with _uninterruptible(signal.SIGTERM, signal.SIGINT):
                preserved = _preserve_ledger(scratch, args.work)
                _rmtree_marked(scratch)
            if preserved is not None:
                print(f"wrote {preserved}")


def _run(args: argparse.Namespace, salt: str, scratch: Path) -> None:
    ledger = scratch / "ledger.jsonl"
    # Empty means every branch Gerrit accepts changes on, exactly as `branch_refs` (and so the
    # CLI's `--via git`) reads an empty `--branch` list; --branch repeated restricts it.
    branches = args.branch

    url = f"{GIT_HOSTS[ORG]}/{PROJECT}"
    repo = Repo.open(scratch / "hardware-interfaces.git", url, Pacer(1.0), ledger=ledger)
    bounds = {month: month_bounds(month) for month in MONTHS}
    earliest = min(_since(start) for start, _ in bounds.values())
    branch_ref_names = branch_refs(repo, ORG, branches)
    marker = scratch / "history-since.txt"
    held = _history_fetched(marker, branch_ref_names, earliest)
    if held is None:
        walked, dormant = fetch_history(repo, branch_ref_names, earliest)
        _record_history_fetch(marker, branch_ref_names, earliest, walked)
    else:
        # A rerun the marker already covers: `fetch_history` did not run this time, so the
        # branches actually walked are whatever an earlier run over this same scratch
        # directory recorded, restricted to what this run asked for.
        walked = sorted(set(held.get("walked", branch_ref_names)) & set(branch_ref_names))
        dormant = len(branch_ref_names) - len(walked)
    listing = scratch / "refs-changes.tsv"
    if not listing.is_file():
        pairs = repo.list_remote("patch_set_refs", "refs/changes/*")
        listing.write_text("".join(f"{oid}\t{ref}\n" for oid, ref in pairs))
    change_refs = [tuple(line.split("\t")) for line in listing.read_text().splitlines() if line]
    by_commit = patch_set_commits(repo, change_refs)

    on_branch = (
        {
            line.split()[0]: int(line.split()[1])
            for line in repo.git("log", "--format=%H %ct", *walked).decode().splitlines()
        }
        if walked
        else {}
    )
    candidates: dict[int, MergedCommit] = {}
    enumeration_counts = {}
    for month, (start, end) in bounds.items():
        found, counts = merged_commits(
            repo,
            walked,
            _since(start),
            end,
            review_host=REVIEW_HOSTS[ORG],
            project=PROJECT,
            by_commit=by_commit,
        )
        enumeration_counts[month] = counts
        candidates.update({m.number: m for m in found if m.number is not None})

    rows, collect_counts = collect(
        repo,
        sorted(candidates),
        project=PROJECT,
        merged=candidates,
        submitted_between=collect_span(bounds, MONTHS),
    )
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
            **classify_enumeration(month, in_month, rest, on_branch, walked),
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
        # Every branch the project could put a change on (`branch_refs`'s own result), not
        # `--branch` as requested: a requested branch the project has none of would otherwise
        # read as having been read. `branches_walked` is the subset `fetch_history` actually
        # fetched full history for; a branch left out is either dormant (its tip predates the
        # earliest candidate date, counted in `branches_dormant`) or vanished between listing
        # and fetch.
        "branches": branch_ref_names,
        "branches_walked": walked,
        "branches_dormant": dormant,
        "months": list(MONTHS),
        "rest_root": _relative_to_repo(args.rest_root),
        "requests": ledger_totals(ledger),
        "collect": collect_counts,
        "enumeration": enumeration,
        "fields": compare_fields(repo, pairs, meta_ancestry),
        "rest_timing": rest_timing(args.rest_root),
        "rebase": measure_rebases(repo, pairs),
        "examples": compare_examples(
            pairs, rest_examples(args.rest_root), scratch / "example-diffs.jsonl"
        ),
        "examples_refined": compare_examples(
            pairs,
            rest_examples(args.rest_root, "refined"),
            scratch / "example-diffs-refined.jsonl",
            refine_index=index_changes(git for git, _ in pairs),
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
