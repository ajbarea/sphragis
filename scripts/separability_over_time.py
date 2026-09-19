"""Is the organizational signal fading while the study measures it?

Style is drifting toward what models write (Xu et al., arXiv:2506.12014: snake_case function
names in Python from 40.7% in Q1 2023 to 49.8% in Q3 2025), and RQ1 trains on one year and tests
on the next. If an organization's hand is being overwritten, the contrast is attenuated by drift
rather than by an absence of house style, and a null becomes ambiguous.

The generative contrast cannot answer this without new runs, since only the dev window is held
out. The separability probe can: it reads the corpus directly. Two readings, because the first
turned out not to be available here:

  across       how separable the two organizations are, slice of months by slice of months.
               Content must be held to one suffix or the probe reads the language, and the only
               suffix both organizations carry is Python, which Qt stops writing in 2025: 39 to
               122 examples a month in late 2024 against 0 to 31 after. The series that comes out
               of it tracks that collapse, not the hand, and is reported only to show why.
  drift        within ONE organization, earliest months against latest, on its own dominant
               suffix. This asks the question the citation raises directly: is this
               organization's review text moving? Chance is 0.5, and above it means the corpus
               the study trains on is not the corpus it will test on.

It reads every month built for an organization, which includes the dev window where
`scripts/separability.py` confines itself to pilot and train. That is deliberate and the two
probes differ: this one asks whether the corpus moves over time, and a drift reading that stops
before the last two months cannot see the end of the series. It reads corpus text and no
held-out predictions, so the dev window is described rather than spent. The test window is
refused here in code as well as by the fetcher, so a month fetched after acceptance cannot walk
into a descriptive analysis by being on disk.

    uv run --no-sync --no-active python scripts/separability_over_time.py \
        --out datasets/results/separability-over-time.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.pipeline import run_dedup
from sphragis.measure.probe import accuracy_interval, code_shapes, comment_words, documents

parser = argparse.ArgumentParser()
parser.add_argument("--orgs", nargs=2, default=["openstack", "qt"])
parser.add_argument(
    "--suffix", default=".py", help="the one source suffix both organizations carry"
)
parser.add_argument("--months-per-slice", type=int, default=3)
parser.add_argument("--seed", type=int, default=5)
parser.add_argument("--resamples", type=int, default=200)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--mode", default="across", choices=("across", "drift"))
parser.add_argument(
    "--read",
    default="comments",
    choices=("comments", "code"),
    help="the reviewers' words, or the refinement's code read as convention shapes",
)
parser.add_argument(
    "--drift-suffix",
    action="append",
    default=[],
    metavar="ORG=SUFFIX",
    help="the suffix each organization is read on in drift mode, e.g. qt=.cpp",
)


#: No month from the test window is read, whatever is on disk. The seal is a protocol
#: guarantee, so it is enforced where the months are loaded rather than assumed upstream.
SEALED_FROM = WINDOWS["test"][0][:7]


def by_month(org: str) -> dict[str, list[dict[str, Any]]]:
    """Every built month of an organization before the seal, deduplicated within the month."""
    out: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(Path(f"datasets/gerrit/{org}/examples").glob("*.jsonl")):
        if path.stem >= SEALED_FROM:
            print(f"refusing {org} {path.stem}: at or past the sealed window {SEALED_FROM}")
            continue
        rows = [json.loads(line) for line in path.open() if line.strip()]
        kept, _ = run_dedup(rows)
        out[path.stem] = kept
    return out


def drift(args: argparse.Namespace, months: dict[str, dict[str, list[dict[str, Any]]]]) -> dict:
    """Within each organization, its earliest months against its latest."""
    suffixes = dict(entry.split("=", 1) for entry in args.drift_suffix)
    report: dict[str, Any] = {
        "mode": "drift",
        "read": args.read,
        "suffixes": suffixes,
        "sealed_from": SEALED_FROM,
        "organizations": {},
    }
    for org, built in months.items():
        names = sorted(built)
        width = args.months_per_slice
        if len(names) < 2 * width:
            continue
        early, late = names[:width], names[-width:]
        suffix = suffixes.get(org)
        reader = code_shapes if args.read == "code" else comment_words
        docs = documents(
            [row for month in early for row in built[month]], 0, suffix=suffix, words_of=reader
        ) + documents(
            [row for month in late for row in built[month]], 1, suffix=suffix, words_of=reader
        )
        result = accuracy_interval(docs, seed=args.seed, resamples=args.resamples)
        report["organizations"][org] = {"early": early, "late": late, **result}
        print(
            f"{org:<10} {'+'.join(m[-2:] for m in early)} against "
            f"{'+'.join(m[-2:] for m in late)}  {int(result['changes']):4d} changes  "
            f"accuracy {result['accuracy']:.3f} [{result['low']:.3f}, {result['high']:.3f}]",
            flush=True,
        )
    return report


def main() -> None:
    args = parser.parse_args()
    first, second = args.orgs
    months = {org: by_month(org) for org in (first, second)}
    if args.mode == "drift":
        report = drift(args, months)
        args.out.write_text(json.dumps(report, indent=2))
        print(f"wrote {args.out}")
        return
    shared = sorted(set(months[first]) & set(months[second]))
    if not shared:
        raise SystemExit(f"{first} and {second} share no built month")
    slices: dict[str, list[str]] = defaultdict(list)
    for index, month in enumerate(shared):
        slices[f"{shared[index - index % args.months_per_slice]}+"].append(month)

    report: dict[str, Any] = {
        "organizations": [first, second],
        "suffix": args.suffix,
        "months_per_slice": args.months_per_slice,
        "sealed_from": SEALED_FROM,
        "slices": {},
    }
    for name, group in slices.items():
        docs = documents(
            [row for month in group for row in months[first][month]], 0, suffix=args.suffix
        ) + documents(
            [row for month in group for row in months[second][month]], 1, suffix=args.suffix
        )
        result = accuracy_interval(docs, seed=args.seed, resamples=args.resamples)
        report["slices"][name] = {"months": group, **result}
        print(
            f"{name:<10} {'+'.join(m[-2:] for m in group):<9} {int(result['changes']):4d} changes  "
            f"accuracy {result['accuracy']:.3f} [{result['low']:.3f}, {result['high']:.3f}]",
            flush=True,
        )
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
