"""Which projects on a candidate Gerrit host could carry an organization, before collecting it.

Three measurements per project, over the train and dev months, with nothing written into the
corpus: merged changes a month and the share owned by service accounts or rollers; the file
types a sample of human changes touches; and line-anchored inline comments by someone other
than the change owner per sampled change, the quantity examples are built from.

    measure volume  <project> <months>   one listing per month (merged, created in window, human)
    measure ranged  <project> <months>   the same in six sub-ranges a month, for projects past
                                         the host's 10,000-result cap on one query
    measure yield   <project> <month> <k> k human changes sampled with a fixed seed
    summarize <dir> <out>                the feasibility table, the selection rule applied,
                                         and the greedy placebo split of what it admits

Every month from the sealed test window's start onward is refused before any request, by the
same check fetch applies.

The owner filter is for scoping only. The pipeline filters no owners; a roller's change
reaches no example because the author filter and the anchor requirement drop it.

Chromium's raw measurements (2026-09-22, 1,042 requests at one a second) are committed
beside the summary under datasets/results/host-scoping-chromium/.
"""

from __future__ import annotations

import collections
import importlib.util
import itertools
import json
import random
import sys
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from sphragis.corpus.build import is_acknowledgement
from sphragis.corpus.cli import GERRIT, http_transport, refuse_if_sealed
from sphragis.corpus.examples import is_code_file
from sphragis.corpus.gerrit import _get, parse_response
from sphragis.provenance import provenance_header

BASE = GERRIT["chromium"]
WINDOW_START = "2024-11-01"  # the train window's first day; the pilot month is not used
TRAIN_LAST = "2025-08"

# The selection rule, fixed before any per-project yield was read (2026-09-22 21:58 EDT).
# Yield enters as feasibility, never as an outcome.
CPP = frozenset({".cc", ".cpp", ".cxx", ".hpp", ".hh", ".mm"})
MIN_PROJECTED_EXAMPLES = 256  # the RQ2 client length in clients-cpp-256.txt
# Examples per reviewer-anchored comment: Qt's 0.5 comments a change (research log,
# 2026-09-21) against 0.219 examples a fetched change over its train and dev months.
EXAMPLES_PER_COMMENT = 0.42
# The registered split criteria: at least three projects a half, none over half of its half's
# train examples, each half at least OpenStack's smaller placebo half (2,163 train examples
# from its frozen train split under scripts/placebo_corpus.py's rule).
MIN_PROJECTS_A_HALF = 3
MAX_SHARE_OF_HALF = 0.5
MIN_HALF_TRAIN = 2163


def _transport():
    return http_transport(min_interval=1.0)


def get(url: str, transport) -> object:
    return parse_response(_get(url, transport, time.sleep, [0]))


def is_bot(owner: dict) -> bool:
    email = (owner.get("email") or "").lower()
    local = email.split("@")[0]
    name = (owner.get("name") or "").lower()
    return (
        email.endswith("gserviceaccount.com")
        or "autoroll" in local
        or local.endswith("-bot")
        or local.endswith("bot")
        or "autoroll" in name
        or not email
    )


def month_bounds(month: str) -> tuple[str, str]:
    year, mm = map(int, month.split("-"))
    following = f"{year + (mm == 12)}-{1 if mm == 12 else mm + 1:02d}"
    return f"{month}-01", f"{following}-01"


def listing(transport, query: str, opts: list[str], n: int = 500, limit: int | None = None):
    out: list[dict] = []
    start = 0
    while True:
        o = "".join(f"&o={x}" for x in opts)
        page = get(f"{BASE}/changes/?q={quote(query)}&n={n}&S={start}{o}", transport)
        out.extend(page)
        more = bool(page and page[-1].get("_more_changes"))
        if not more or (limit and len(out) >= limit):
            return out, more
        start += len(page)


def _counts(project: str, month: str, rows: list[dict], **extra) -> dict:
    kept = [r for r in rows if r["created"][:10] >= WINDOW_START]
    human = [r for r in kept if not is_bot(r["owner"])]
    return {
        "project": project,
        "month": month,
        "merged": len(rows),
        "created_in_window": len(kept),
        "human": len(human),
        **extra,
    }


def measure_volume(project: str, months: list[str]) -> None:
    transport = _transport()
    for month in months:
        a, b = month_bounds(month)
        q = f"status:merged after:{a} before:{b} project:{project}"
        rows, _ = listing(transport, q, ["DETAILED_ACCOUNTS"])
        print(json.dumps(_counts(project, month, rows)), flush=True)


def measure_ranged(project: str, months: list[str]) -> None:
    transport = _transport()
    for month in months:
        a, b = month_bounds(month)
        cuts = [a] + [f"{month}-{d:02d}" for d in (6, 11, 16, 21, 26)] + [b]
        rows: list[dict] = []
        capped = False
        for lo, hi in itertools.pairwise(cuts):
            q = f"status:merged after:{lo} before:{hi} project:{project}"
            part, _ = listing(transport, q, ["DETAILED_ACCOUNTS"])
            capped |= len(part) >= 10000
            rows.extend(part)
        print(json.dumps(_counts(project, month, rows, capped=capped, ranged=True)), flush=True)


def measure_yield(project: str, month: str, k: int) -> None:
    transport = _transport()
    a, b = month_bounds(month)
    q = f"status:merged after:{a} before:{b} project:{project}"
    opts = ["DETAILED_ACCOUNTS", "CURRENT_REVISION", "CURRENT_FILES"]
    rows, more = listing(transport, q, opts, n=200, limit=200)
    human = [r for r in rows if not is_bot(r["owner"]) and r["created"][:10] >= WINDOW_START]
    random.Random(20260922).shuffle(human)
    sample = human[:k]
    exts: collections.Counter[str] = collections.Counter()
    commented: collections.Counter[str] = collections.Counter()
    anchored = reviewer = strict = 0
    for change in sample:
        revision = change["revisions"][change["current_revision"]]
        revisions = revision["_number"]
        for path in revision.get("files", {}):
            exts[PurePosixPath(path).suffix.lower() or "(none)"] += 1
        comments = get(f"{BASE}/changes/{change['_number']}/comments", transport)
        owner = change["owner"]["_account_id"]
        for path, thread in comments.items():
            if not is_code_file(path):
                continue
            for c in thread:
                if not isinstance(c.get("line"), int):
                    continue
                anchored += 1
                if c.get("author", {}).get("_account_id") == owner:
                    continue
                reviewer += 1
                commented[PurePosixPath(path).suffix.lower() or "(none)"] += 1
                message = str(c.get("message", ""))
                if not is_acknowledgement(message) and c.get("patch_set", revisions) < revisions:
                    strict += 1
    print(
        json.dumps(
            {
                "project": project,
                "month": month,
                "listed": len(rows),
                "listed_more": more,
                "human_listed": len(human),
                "sampled": len(sample),
                "anchored": anchored,
                "by_reviewer": reviewer,
                "strict": strict,
                "file_ext": exts.most_common(),
                "commented_ext": commented.most_common(),
            }
        ),
        flush=True,
    )


def _greedy_halves(train: dict[str, float]) -> list[dict]:
    spec = importlib.util.spec_from_file_location(
        "placebo_corpus", Path(__file__).with_name("placebo_corpus.py")
    )
    assert spec and spec.loader
    placebo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(placebo)
    counts = {p: round(n) for p, n in train.items()}
    side = placebo.assign(counts)
    halves = []
    for s in (0, 1):
        members = {p: n for p, n in counts.items() if side[p] == s}
        total = sum(members.values())
        largest = max(members, key=lambda p: members[p]) if members else None
        share = members[largest] / total if largest and total else 1.0
        halves.append(
            {
                "projects": sorted(members),
                "train_examples": total,
                "largest": largest,
                "largest_share": round(share, 3),
                "qualifies": len(members) >= MIN_PROJECTS_A_HALF
                and share <= MAX_SHARE_OF_HALF
                and total >= MIN_HALF_TRAIN,
            }
        )
    return halves


def summarize(directory: Path, out: Path) -> None:
    volume: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    for line in (directory / "volume.jsonl").read_text().splitlines():
        row = json.loads(line)
        # A plain listing that reached 10,000 was cut by the host; only ranged counts stand.
        if "month" in row and (row.get("ranged") or row["merged"] < 10000):
            volume[row["project"]][row["month"]] = row
    samples = {
        (row := json.loads(line))["project"]: row
        for line in (directory / "yield.jsonl").read_text().splitlines()
        if line.strip()
    }
    projects: dict[str, dict] = {}
    for project, sample in samples.items():
        months = volume[project]
        measured = sorted(months)
        human_mo = sum(r["human"] for r in months.values()) / len(months)
        merged_mo = sum(r["merged"] for r in months.values()) / len(months)
        if len(months) == 12:
            train_h = sum(r["human"] for m, r in months.items() if m <= TRAIN_LAST)
            all_h = sum(r["human"] for r in months.values())
        else:  # the ranged months stand for the window's twelve: ten train, two dev
            train_h, all_h = human_mo * 10, human_mo * 12
        ext = dict(sample["file_ext"])
        total = sum(ext.values())
        has_cc = any(e in ext for e in CPP)
        cpp_files = sum(n for e, n in ext.items() if e in CPP) + (ext.get(".h", 0) if has_cc else 0)
        other = max(
            (n for e, n in ext.items() if e not in CPP and not (has_cc and e == ".h")), default=0
        )
        rate = sample["by_reviewer"] / sample["sampled"] if sample["sampled"] else 0.0
        projects[project] = {
            "months_counted": measured,
            "merged_per_month": round(merged_mo),
            "human_per_month": round(human_mo),
            "bot_share": round(1 - human_mo / merged_mo, 3) if merged_mo else None,
            "sampled_changes": sample["sampled"],
            "reviewer_anchored_per_change": round(rate, 2),
            "strict_per_change": round(sample["strict"] / sample["sampled"], 2)
            if sample["sampled"]
            else 0.0,
            "cpp_file_share": round(cpp_files / total, 3) if total else 0.0,
            "cpp_dominant": has_cc and cpp_files > other,
            "projected_train_examples": round(train_h * rate * EXAMPLES_PER_COMMENT),
            "projected_window_examples": round(all_h * rate * EXAMPLES_PER_COMMENT),
            "file_ext": sample["file_ext"],
        }
    admitted = sorted(
        (
            p
            for p, r in projects.items()
            if r["cpp_dominant"] and r["projected_window_examples"] >= MIN_PROJECTED_EXAMPLES
        ),
        key=lambda p: -projects[p]["projected_train_examples"],
    )
    train = {p: projects[p]["projected_train_examples"] for p in admitted}
    qualifying = [
        list(subset)
        for k in range(2 * MIN_PROJECTS_A_HALF, len(admitted) + 1)
        for subset in itertools.combinations(admitted, k)
        if all(h["qualifies"] for h in _greedy_halves({p: train[p] for p in subset}))
    ]
    report = {
        "provenance": provenance_header(),
        "host": BASE,
        "rule": {
            "cpp_extensions": sorted(CPP),
            "min_projected_window_examples": MIN_PROJECTED_EXAMPLES,
            "examples_per_reviewer_comment": EXAMPLES_PER_COMMENT,
            "split": {
                "min_projects_a_half": MIN_PROJECTS_A_HALF,
                "max_share_of_half": MAX_SHARE_OF_HALF,
                "min_half_train_examples": MIN_HALF_TRAIN,
            },
        },
        "projects": projects,
        "admitted": admitted,
        "split_of_admitted": _greedy_halves(train),
        "split_without_largest": _greedy_halves({p: train[p] for p in admitted[1:]}),
        "qualifying_subsets": qualifying,
    }
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out}: admitted {admitted}, qualifying subsets {qualifying}")


def main(argv: list[str]) -> None:
    if argv[:1] == ["summarize"]:
        summarize(Path(argv[1]), Path(argv[2]))
        return
    if argv[:1] != ["measure"] or len(argv) < 4:
        raise SystemExit(__doc__)
    kind, project, arg = argv[1], argv[2], argv[3]
    # The same refusal fetch applies, before any request: every month from the sealed test
    # window's start onward, under the corpus root's seal for this host.
    for month in arg.split(","):
        refuse_if_sealed(Path("datasets/gerrit"), "chromium", month)
    if kind == "volume":
        measure_volume(project, arg.split(","))
    elif kind == "ranged":
        measure_ranged(project, arg.split(","))
    elif kind == "yield":
        measure_yield(project, arg, int(argv[4]))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
