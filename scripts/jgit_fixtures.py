"""Derive `tests/fixtures/jgit_edits.json` from JGit itself, so the port is checked against JGit.

Each case's inputs live in the fixture; this script asks JGit for the edits and the fallback flag
and writes them back, recording the jar it ran. `--check` fails if the committed file differs from
what JGit returns, which is how the fixture's provenance is reproduced rather than asserted.

    uv run --no-sync python scripts/jgit_fixtures.py --jar JGIT.jar --check
    uv run --no-sync python scripts/jgit_fixtures.py --jar JGIT.jar --add cases.tsv

`--add` takes one case a line, BASE64(a) TAB BASE64(b).

Needs a JDK 11+ (`java` runs the single-file harness in scripts/jgit/). Maven Central:
https://repo1.maven.org/maven2/org/eclipse/jgit/org.eclipse.jgit/7.8.0.202609011348-r/
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "jgit_edits.json"
HARNESS = REPO / "scripts" / "jgit" / "JgitEdits.java"
JAR_NAME = "org.eclipse.jgit-7.8.0.202609011348-r.jar"
JAR_SHA256 = "cc63976f92e8058d05a543f320a6237accf2f17b745ab20946f246ac0b54dfd6"


def jgit(jar: Path, cases: list[tuple[str, str]]) -> list[dict]:
    lines = "".join(
        base64.b64encode(a.encode()).decode() + "\t" + base64.b64encode(b.encode()).decode() + "\n"
        for a, b in cases
    )
    out = subprocess.run(
        ["java", "-cp", str(jar), str(HARNESS)],
        input=lines,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    if len(out) != len(cases):
        raise SystemExit(f"JGit answered {len(out)} of {len(cases)} cases")
    return [json.loads(line) for line in out]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar", type=Path, required=True, help=JAR_NAME)
    parser.add_argument("--add", type=Path, help="new cases, one BASE64(a) TAB BASE64(b) per line")
    parser.add_argument("--check", action="store_true", help="fail if the fixture is not JGit's")
    args = parser.parse_args()

    digest = hashlib.sha256(args.jar.read_bytes()).hexdigest()
    if digest != JAR_SHA256:
        raise SystemExit(f"{args.jar}: sha256 {digest}, expected {JAR_SHA256} ({JAR_NAME})")

    fixture = json.loads(FIXTURE.read_text())
    cases = [(c["a"], c["b"]) for c in fixture["cases"]]
    if args.add:
        for line in args.add.read_text().splitlines():
            a, b = (base64.b64decode(part).decode() for part in line.split("\t"))
            if (a, b) not in cases:
                cases.append((a, b))

    answers = jgit(args.jar, cases)
    derived = {
        **{k: v for k, v in fixture.items() if k != "cases"},
        "source": (
            f"{JAR_NAME} (sha256 {JAR_SHA256}), HistogramDiff().diff(RawTextComparator."
            "WS_IGNORE_CHANGE, new RawText(a), new RawText(b)); myers_fallback: the edits differ "
            "from the same diff with setFallbackAlgorithm(null)"
        ),
        "harness": "scripts/jgit/JgitEdits.java via scripts/jgit_fixtures.py",
        "cases": [
            {"a": a, "b": b, "myers_fallback": r["myers_fallback"], "edits": r["edits"]}
            for (a, b), r in zip(cases, answers, strict=True)
        ],
    }
    if args.check:
        mismatched = [
            i
            for i, (old, new) in enumerate(zip(fixture["cases"], derived["cases"], strict=True))
            if old["edits"] != new["edits"] or old["myers_fallback"] != new["myers_fallback"]
        ]
        print(
            f"{len(fixture['cases'])} cases, {len(mismatched)} differ from JGit {mismatched[:10]}"
        )
        return 1 if mismatched else 0
    FIXTURE.write_text(json.dumps(derived, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {FIXTURE.relative_to(REPO)}: {len(cases)} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
