"""An open-weight, pinned model as a further blind rater on the label audit.

Raters A and B (Claude Opus 5.5 and Sonnet 5) ran through an API, so their labels cannot be
reproduced once those models are retired; a rater at pinned open weights can be rerun by anyone.
It labels the same audited examples as A, B and the human, under the committed rubric (v2), seeing
only the fields they saw (`label_audit_blind.SHOWN`: no organization, project, window or half), and
answers the rubric's two questions as JSON. Its agreement with each of them is read with the same
statistics as the audit.

Three steps, because the items are corpus text and never committed:

    # 1. local: the audited examples' shown fields, from the corpora, into scratch
    uv run --no-sync --no-active python scripts/label_audit_open_rater.py prepare \\
        --root ../wm-bots/datasets/gerrit --items scratch/open-rater/items.jsonl
    # 2. on a GPU (scripts/label_audit_open_rater.sbatch): one label per item
    uv run --no-sync python scripts/label_audit_open_rater.py rate \\
        --items items.jsonl --out open-rater-labels.json
    # 3. local: agreement with A, B and the human
    uv run --no-sync --no-active python scripts/label_audit_open_rater.py agree \\
        --labels open-rater-labels.json --out datasets/results/label-audit-v2-open-rater.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from label_audit_blind import SHOWN  # noqa: E402
from label_audit_sample import LABELS  # noqa: E402

from sphragis.corpus.load import refined_examples  # noqa: E402
from sphragis.provenance import provenance_header  # noqa: E402

AUDIT = Path("datasets/results/label-audit-v2.json")
RUBRIC = Path(__file__).resolve().parent / "label_audit" / "rubric.md"
# Qwen3-14B (Apache 2.0) fits a 40 GB A100 in bf16; pinned, as every checkpoint is.
MODEL = "Qwen/Qwen3-14B"
MODEL_REVISION = "40c069824f4251a91eefaf281ebe4c544efd3e18"
MAX_NEW_TOKENS = 64
_ANSWER = re.compile(r"\{.*?\}", re.S)

parser = argparse.ArgumentParser(description=__doc__)
sub = parser.add_subparsers(dest="step", required=True)
prep = sub.add_parser("prepare")
prep.add_argument("--root", type=Path, required=True, help="gerrit root with <org>/refined")
prep.add_argument("--audit", type=Path, default=AUDIT)
prep.add_argument("--items", type=Path, required=True)
rate = sub.add_parser("rate")
rate.add_argument("--items", type=Path, required=True)
rate.add_argument("--out", type=Path, required=True, help="the labels file")
agree = sub.add_parser("agree")
agree.add_argument("--labels", type=Path, required=True)
agree.add_argument("--audit", type=Path, default=AUDIT)
agree.add_argument("--out", type=Path, required=True)


def prompt(rubric: str, item: Mapping[str, Any]) -> str:
    """The rubric, then one item's shown fields, then the answer format."""
    shown = {field: item.get(field, "") for field in SHOWN}
    return (
        f"{rubric}\n\n---\n\nThe item:\n{json.dumps(shown, indent=1)}\n\n"
        f"Answer with one JSON object and nothing else: "
        f'{{"label": one of {list(LABELS)}, "outside_names": true or false}}'
    )


def parse(text: str) -> dict[str, Any] | None:
    """The first JSON object in a reply, if it carries a rubric label and a boolean flag."""
    for match in _ANSWER.finditer(text):
        try:
            answer = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if answer.get("label") in LABELS and isinstance(answer.get("outside_names"), bool):
            return {"label": answer["label"], "outside_names": answer["outside_names"]}
    return None


def do_prepare(args: argparse.Namespace) -> None:
    wanted = set(json.loads(args.audit.read_text())["labels"])
    found: dict[str, dict] = {}
    # The refined examples, as the audit sampled them (`label_audit_sample.py`): refine rewrites
    # comments (bots, acknowledgements), so a built row could show another comment set.
    for org in sorted({i.split(":", 1)[0] for i in wanted}):
        for row in refined_examples(args.root, org):
            if row["id"] in wanted:
                if row["id"] in found:
                    raise SystemExit(f"{row['id']} appears twice in the refined corpus")
                found[row["id"]] = {"id": row["id"], **{f: row.get(f, "") for f in SHOWN}}
    # The audit drew from corpus v2; an example a later refinement removed has no current row,
    # and is left out rather than rebuilt, so every item is shown exactly as its raters saw it.
    missing = sorted(wanted - set(found))
    args.items.parent.mkdir(parents=True, exist_ok=True)
    args.items.write_text("".join(json.dumps(found[i]) + "\n" for i in sorted(found)))
    args.items.with_suffix(".missing.json").write_text(json.dumps(missing, indent=1) + "\n")
    print(f"wrote {len(found)} items to {args.items}; {len(missing)} no longer refined")


def do_rate(args: argparse.Namespace) -> None:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    rubric = RUBRIC.read_text()
    items = [json.loads(line) for line in args.items.read_text().splitlines()]
    tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=MODEL_REVISION)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL, revision=MODEL_REVISION, dtype=torch.bfloat16, device_map="cuda:0"
    )
    model.eval()
    labels: dict[str, Any] = {}
    unparsed = 0
    for item in items:
        text = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt(rubric, item)}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
        reply = tokenizer.decode(out[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True)
        answer = parse(reply)
        if answer is None:
            unparsed += 1
            continue
        labels[item["id"]] = answer
    args.out.write_text(
        json.dumps(
            {
                "model": MODEL,
                "revision": MODEL_REVISION,
                "items": len(items),
                "unparsed": unparsed,
                "labels": labels,
                "provenance": provenance_header(),
            },
            indent=1,
        )
        + "\n"
    )
    print(f"{len(labels)} labelled, {unparsed} unparsed; wrote {args.out}")


def do_agree(args: argparse.Namespace) -> None:
    from label_audit_agreement import _pair

    audit = json.loads(args.audit.read_text())
    rated = json.loads(args.labels.read_text())
    open_labels = rated["labels"]
    report: dict[str, Any] = {
        "model": rated["model"],
        "revision": rated["revision"],
        "items": rated["items"],
        "unparsed": rated["unparsed"],
        "pairs": {},
    }
    # An unparsed item is left out of every pair, so each pair records its own item count.
    for other in ("A", "B", "human"):
        common = sorted(i for i, v in audit["labels"].items() if other in v and i in open_labels)
        mine = {i: open_labels[i]["label"] for i in common}
        theirs = {i: audit["labels"][i][other]["label"] for i in common}
        report["pairs"][f"open-{other}"] = {
            "label": _pair(mine, theirs, common, list(LABELS)),
            "valid": _pair(
                {i: v == "valid" for i, v in mine.items()},
                {i: v == "valid" for i, v in theirs.items()},
                common,
                [True, False],
            ),
            "outside_names": _pair(
                {i: open_labels[i]["outside_names"] for i in common},
                {i: bool(audit["labels"][i][other]["outside_names"]) for i in common},
                common,
                [True, False],
            ),
        }
    # The audit's own pairs on the items the open rater and the human share, so every human
    # figure has a comparator on the same items.
    shared = sorted(i for i, v in audit["labels"].items() if "human" in v and i in open_labels)
    report["comparators_on_shared_human_items"] = {
        f"{x}-{y}": _pair(
            {i: audit["labels"][i][x]["label"] for i in shared},
            {i: audit["labels"][i][y]["label"] for i in shared},
            shared,
            list(LABELS),
        )
        for x, y in (("A", "human"), ("B", "human"), ("A", "B"))
    }
    # Adopted when its label kappa with the human reaches the lower end of the locked human-A
    # interval (research log, 2026-10-02), and only with every item parsed.
    bar = audit["pairs"]["human~A"]["kappa_95"][0]
    report["adoption"] = {
        "bar": bar,
        "bar_source": "label-audit-v2.json pairs['human~A'].kappa_95[0], locked, label",
        "adopted": rated["unparsed"] == 0
        and report["pairs"]["open-human"]["label"]["kappa"] >= bar,
    }
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    for name, pair in report["pairs"].items():
        print(name, pair["label"]["items"], f"kappa {pair['label']['kappa']:.3f}")
    print(f"wrote {args.out}")


def main() -> None:
    args = parser.parse_args()
    {"prepare": do_prepare, "rate": do_rate, "agree": do_agree}[args.step](args)


if __name__ == "__main__":
    main()
