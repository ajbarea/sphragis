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

Runs on a CPU in under a minute. Reads the pilot and train windows only, so the dev window
is not spent on a descriptive analysis and the test window is untouched.

Run: uv run --no-active python scripts/separability.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.pipeline import run_dedup
from sphragis.measure.probe import accuracy_interval, documents

RESULTS = Path("datasets/results/separability.json")
ORGS = ("openstack", "qt")
SUFFIX = ".py"  # the only source suffix both organizations carry in quantity
READ_WINDOWS = ("pilot", "train")
SEED = 5
RESAMPLES = 200


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
    print(
        f"{name:<46} {int(result['changes']):5d} changes  "
        f"accuracy {result['accuracy']:.3f} [{result['low']:.3f}, {result['high']:.3f}]"
    )


def main() -> None:
    corpora = {org: load(org) for org in ORGS}
    for org, rows in corpora.items():
        suffixes = Counter(Path(str(r["path"])).suffix.lower() for r in rows)
        print(f"{org}: {len(rows)} deduplicated examples, top suffixes {suffixes.most_common(4)}")

    out: dict[str, Any] = {"suffix": SUFFIX, "windows": list(READ_WINDOWS), "seed": SEED}
    first, second = ORGS

    print("\n=== 1. raw: any file type (expected to read the language, not the organization) ===")
    report(
        "raw",
        documents(corpora[first], 0, suffix=None) + documents(corpora[second], 1, suffix=None),
        out,
    )

    print(f"\n=== 2. content-matched: {SUFFIX} only ===")
    matched = documents(corpora[first], 0, suffix=SUFFIX) + documents(
        corpora[second], 1, suffix=SUFFIX
    )
    report(f"matched{SUFFIX}", matched, out)

    print(f"\n=== 3. within-organization baseline: two projects, one org, {SUFFIX} only ===")
    for org in ORGS:
        by_project: dict[str, list[dict[str, Any]]] = {}
        for row in corpora[org]:
            if str(row["path"]).endswith(SUFFIX):
                by_project.setdefault(str(row["project"]), []).append(row)
        ranked = sorted(by_project, key=lambda k: -len(by_project[k]))[:2]
        if len(ranked) < 2:
            continue
        docs = documents(by_project[ranked[0]], 0, suffix=SUFFIX) + documents(
            by_project[ranked[1]], 1, suffix=SUFFIX
        )
        report(f"within:{org}:{ranked[0]} vs {ranked[1]}", docs, out)

    print(
        "\nReading: the cross-organization number means something only to the extent it "
        "exceeds\nthe within-organization baselines. Equal accuracy means the probe is "
        "reading the\ncodebase, and organization is the wrong altitude to look for it."
    )
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    main()
