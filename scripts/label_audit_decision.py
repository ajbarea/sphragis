"""A decision model as a third blind rater on the label audit, through Ollama's System One API.

Asks each blind item the rubric's two questions in one request: the label as a choice over the
rubric's five labels, each described by the rubric's own line for it, and `outside_names` as a
yes/no question. The model sees what rater A and B saw: the rubric, split at its question headings
(the intro and worked example go with both questions), and the item's shown fields as the state.
It never sees the key, other raters' labels or the human's checks.

Writes the raters' labels schema, so `label_audit_agreement.py --rater C=...` reads it unchanged,
plus each answer's probabilities and the model's confidence. `outside_names` is the more probable
of its two answers. The labels are keyed by neutral item id and hold no corpus text, but they sit
beside the sheet under `scratch/`, as the other raters' do.

    uv run --no-sync --no-active python scripts/label_audit_decision.py \\
        --audit-dir scratch/audit-v2 --model nimble --rater C
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from label_audit_sample import LABELS  # noqa: E402

from sphragis.provenance import provenance_header  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--audit-dir", type=Path, required=True, help="rubric-v2.md and batches/")
parser.add_argument("--model", required=True, help="a local System One model, e.g. nimble")
parser.add_argument("--rater", required=True, help="the rater's name: writes raters/NAME/")
parser.add_argument("--url", default="http://localhost:11434")
# Long enough for a 9B model's first load from disk on this machine; a request is under a second.
parser.add_argument("--timeout", type=float, default=300)


def questions(rubric: str) -> dict:
    """The rubric's two questions as System One questions, in the rubric's own words."""
    intro, rest = rubric.split("QUESTION 1:", 1)
    first, rest = rest.split("QUESTION 2:", 1)
    second, example = rest.split("Worked example.", 1)
    example = "Worked example." + example
    described = dict(re.findall(r"^- (\w+): (.+)$", first, flags=re.MULTILINE))
    if set(described) != set(LABELS):
        raise SystemExit(f"the rubric describes {sorted(described)}, not the labels {LABELS}")
    return {
        "label": {
            "type": "choice",
            "instructions": "\n\n".join([intro.strip(), "QUESTION 1:" + first.strip(), example]),
            "criteria": {label: described[label] for label in LABELS},
        },
        "outside_names": {
            "type": "noul",
            "instructions": "\n\n".join([intro.strip(), "QUESTION 2:" + second.strip(), example]),
        },
    }


def ask(url: str, body: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        f"{url}/v1/systemone",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def model_digest(url: str, model: str) -> str | None:
    """The pulled model's digest, so the labels name the exact weights that made them."""
    with urllib.request.urlopen(f"{url}/api/tags", timeout=30) as response:
        tags = json.loads(response.read())["models"]
    return next((m["digest"] for m in tags if m["name"].split(":")[0] == model), None)


def main() -> None:
    args = parser.parse_args()
    asked = questions((args.audit_dir / "rubric-v2.md").read_text())
    items = [
        json.loads(line)
        for batch in sorted((args.audit_dir / "batches").glob("batch-*.jsonl"))
        for line in batch.read_text().splitlines()
        if line
    ]
    with urllib.request.urlopen(f"{args.url}/api/version", timeout=30) as response:
        version = json.loads(response.read())["version"]
    labels: dict[str, dict] = {}
    for n, item in enumerate(items, 1):
        state = {field: value for field, value in item.items() if field != "item"}
        body = {"model": args.model, "state": state, "questions": asked}
        answers = ask(args.url, body, args.timeout)["answers"]
        label, outside = answers["label"], answers["outside_names"]
        labels[item["item"]] = {
            "label": label["choice"],
            # The more probable answer; an exact tie reads as false.
            "outside_names": outside["noul"] > 0.5,
            "probabilities": label["probabilities"],
            "confidence": label["confidence"],
            "outside_names_probability": outside["noul"],
        }
        if n % 50 == 0:
            print(f"{n} of {len(items)} labelled", flush=True)
    out = args.audit_dir / "raters" / args.rater
    out.mkdir(parents=True, exist_ok=True)
    (out / "labels.json").write_text(json.dumps(labels, indent=1) + "\n")
    meta = {
        "model": args.model,
        "digest": model_digest(args.url, args.model),
        "ollama": version,
        "questions": asked,
        "items": len(labels),
        "provenance": provenance_header(),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"{len(labels)} items labelled by {args.model} (ollama {version}); wrote {out}")


if __name__ == "__main__":
    main()
