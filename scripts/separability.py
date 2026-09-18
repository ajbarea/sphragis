"""Can a classifier tell the organizations apart from their review text?

The prior question to RQ1. If organizational identity is not decodable at all, then RQ1's
small transfer advantage is not an adapter failing to use a signal; there is no signal. If
it is decodable, the gap between what a probe reads and what an adapter uses is itself the
finding, and a known one: probes routinely detect information a generative model does not
use.

Three conditions, because accuracy alone means nothing here.

  raw          any file type. OpenStack is 47% Python and Qt 49% C++, so this mostly reads
               the language and is reported only to show how large that shortcut is.
  matched      one file suffix present in both organizations. Content is held roughly
               fixed, so what is left is closer to style.
  within       two projects inside ONE organization, same suffix. The calibration: this is
               what "different codebase, same organization" scores, and the cross-organization
               probe has to beat it before it says anything about organizations.

Two readings of each condition, because which surface carries a fingerprint is a separate
question from whether one exists. Ghaleb (MSR '26, arXiv:2601.17406) finds that for AI coding
agents the commit-message conventions carry more of the signal than the code changes, the reverse
of what human authorship studies report; if an organization's mark likewise sits in the review
text rather than in the code, RQ1 is contrasting a different surface than its framing claims.

  comments     the reviewers' words.
  code         the refinement's code, read as convention shapes (snake_case, braces, f-strings)
               rather than as tokens, so it cannot read the identifiers back as vocabulary.

Runs on a CPU in under a minute. Reads the pilot and train windows only, so the dev window
is not spent on a descriptive analysis and the test window is untouched.

    uv run --no-active python scripts/separability.py
    uv run --no-active python scripts/separability.py --orgs aosp qt --suffix .cpp --read code
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.pipeline import run_dedup
from sphragis.measure.probe import accuracy_interval, code_shapes, comment_words, documents

READ_WINDOWS = ("pilot", "train")
SEED = 5
RESAMPLES = 200
READERS = {"comments": comment_words, "code": code_shapes}

parser = argparse.ArgumentParser()
parser.add_argument("--orgs", nargs=2, default=["openstack", "qt"])
# The default is the only source suffix OpenStack and Qt both carry in quantity.
parser.add_argument("--suffix", default=".py")
parser.add_argument("--read", default="comments", choices=sorted(READERS))
parser.add_argument("--out", type=Path, default=None)


def load(org: str) -> list[dict[str, Any]]:
    """Deduplicated examples from the windows this analysis is allowed to read."""
    first = min(WINDOWS[w][0] for w in READ_WINDOWS)
    last = max(WINDOWS[w][1] for w in READ_WINDOWS)
    rows: list[dict[str, Any]] = []
    for path in sorted(Path(f"datasets/gerrit/{org}/examples").glob("*.jsonl")):
        if not (first[:7] <= path.stem < last[:7]):
            continue
        rows.extend(json.loads(line) for line in path.open() if line.strip())
    kept, _ = run_dedup(rows)
    return kept


def report(name: str, docs, out: dict[str, Any]) -> None:
    result = accuracy_interval(docs, seed=SEED, resamples=RESAMPLES)
    out[name] = result
    if math.isnan(result["accuracy"]):
        print(
            f"{name:<46} {int(result['documents']):5d} documents, too few per label to score: "
            "reported as refused, not as a number"
        )
        return
    print(
        f"{name:<46} {int(result['changes']):5d} changes  "
        f"accuracy {result['accuracy']:.3f} [{result['low']:.3f}, {result['high']:.3f}]"
    )


def main() -> None:
    args = parser.parse_args()
    orgs, suffix = tuple(args.orgs), args.suffix
    reader = READERS[args.read]
    results = args.out or Path(
        "datasets/results/separability"
        + (
            ""
            if list(orgs) == ["openstack", "qt"] and suffix == ".py"
            else f"-{'-'.join(orgs)}{suffix}"
        )
        + ("" if args.read == "comments" else f"-{args.read}")
        + ".json"
    )
    corpora = {org: load(org) for org in orgs}
    for org, rows in corpora.items():
        suffixes = Counter(Path(str(r["path"])).suffix.lower() for r in rows)
        print(f"{org}: {len(rows)} deduplicated examples, top suffixes {suffixes.most_common(4)}")

    out: dict[str, Any] = {
        "suffix": suffix,
        "organizations": list(orgs),
        "read": args.read,
        "windows": list(READ_WINDOWS),
        "seed": SEED,
    }
    first, second = orgs

    print("\n=== 1. raw: any file type (expected to read the language, not the organization) ===")
    report(
        "raw",
        documents(corpora[first], 0, suffix=None, words_of=reader)
        + documents(corpora[second], 1, suffix=None, words_of=reader),
        out,
    )

    print(f"\n=== 2. content-matched: {suffix} only ===")
    matched = documents(corpora[first], 0, suffix=suffix, words_of=reader) + documents(
        corpora[second], 1, suffix=suffix, words_of=reader
    )
    report(f"matched{suffix}", matched, out)

    print(f"\n=== 3. within-organization baseline: two projects, one org, {suffix} only ===")
    for org in orgs:
        by_project: dict[str, list[dict[str, Any]]] = {}
        for row in corpora[org]:
            if str(row["path"]).endswith(suffix):
                by_project.setdefault(str(row["project"]), []).append(row)
        ranked = sorted(by_project, key=lambda k: -len(by_project[k]))[:2]
        if len(ranked) < 2:
            continue
        docs = documents(by_project[ranked[0]], 0, suffix=suffix, words_of=reader) + documents(
            by_project[ranked[1]], 1, suffix=suffix, words_of=reader
        )
        report(f"within:{org}:{ranked[0]} vs {ranked[1]}", docs, out)

    print(
        "\nReading: the cross-organization number means something only to the extent it "
        "exceeds\nthe within-organization baselines. Equal accuracy means the probe is "
        "reading the\ncodebase, and organization is the wrong altitude to look for it."
    )
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {results}")


if __name__ == "__main__":
    main()
