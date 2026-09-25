"""Run every check a pilot job depends on, on the login node, before queueing anything.

CPU only, no GPU allocation, no queue. Exercises the real import path and the real data
pipeline on every corpus the job will read, and confirms the checkout is the pushed commit.

    preflight_pilot.py --script scripts/pilot.py ~/pilot-examples.jsonl
    preflight_pilot.py --script scripts/rq1_pilot.py openstack=~/corpus/a.jsonl qt=~/corpus/b.jsonl

Every check here has to be able to fail. An earlier version compared the checkout's SHA to
itself, required a hand-copied script the deploy discipline forbids, and validated
`TrainingArguments` keywords for a trainer no pilot uses.
"""

import argparse
import subprocess
from pathlib import Path

from sphragis.corpus.load import derived_file_rows
from sphragis.experiment.preflight import check_paths_exist, check_repo_matches

REPO = Path(__file__).resolve().parents[1]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("corpora", nargs="+", help="PATH, or ORG=PATH for a multi-organization job")
parser.add_argument("--script", type=Path, default=Path("scripts/pilot.py"))
parser.add_argument("--split-seed", type=int, default=0)
parser.add_argument(
    "--legacy-corpus",
    action="store_true",
    help="read corpus files not cut under the current label rules, to reproduce an earlier "
    "result; recorded in the output",
)
args = parser.parse_args()

corpora = {
    (entry.split("=", 1)[0] if "=" in entry else "corpus"): Path(entry.split("=", 1)[-1])
    for entry in args.corpora
}
problems: list[str] = []

# 1. Inputs exist and are non-empty, and job output has somewhere to go. Slurm does not
# create the --output directory; a job that cannot open its log dies after the queue wait.
problems += check_paths_exist(
    {
        **{f"corpus {org}": path for org, path in corpora.items()},
        "driver script": REPO / args.script,
        "logs": REPO / "logs",
    }
)

# 2. The imports the job will make actually resolve here.
try:
    from transformers import AutoTokenizer

    from sphragis.corpus.pipeline import run_dedup
    from sphragis.experiment.holdout import holdout_by_change, verbatim_overlap
    from sphragis.experiment.model import MODEL_ID, TRAINING
    from sphragis.experiment.runner import build_prompt
    from sphragis.experiment.training import build_supervised, render_chat
    from sphragis.measure.stats import MIN_CLUSTERS
except Exception as error:
    problems.append(f"import failed: {type(error).__name__}: {error}")
    print("\n".join(problems))
    raise SystemExit(1) from error

# 3. For every corpus: the data path works on every real example, nothing is silently
# refused, the held-out split leaks no pair, and it has enough changes for the bootstrap
# to produce an interval at all. That last one is the cheapest way to learn a month is too
# small: here, rather than at the end of a two-hour allocation.
tok = None
for org, path in corpora.items():
    try:
        if tok is None:
            tok = AutoTokenizer.from_pretrained(MODEL_ID)
        rows = derived_file_rows(path, legacy=args.legacy_corpus)
        kept, removed = run_dedup(rows)
        train, held_out = holdout_by_change(kept, seed=args.split_seed)
        refused = 0
        for r in train:
            try:
                item = build_supervised(
                    tok, r, prompt_builder=build_prompt, max_length=TRAINING["max_seq_length"]
                )
            except ValueError:
                refused += 1
                continue
            assert len(item["input_ids"]) == len(item["labels"]) == len(item["attention_mask"])
            assert any(x != -100 for x in item["labels"]), "no supervised tokens"
        held_changes = len({r["change_id"] for r in held_out})
        leaked = verbatim_overlap(train, held_out)
        print(
            f"{org}: {len(kept)} after dedup {dict(removed)}, {len(train)} train / "
            f"{len(held_out)} held out over {held_changes} changes, {refused} refused"
        )
        if refused:
            problems.append(f"{org}: {refused} training examples exceed max_seq_length")
        if leaked:
            problems.append(f"{org}: {len(leaked)} held-out examples repeat a training pair")
        if held_changes < MIN_CLUSTERS:
            problems.append(
                f"{org}: {held_changes} held-out changes, below the bootstrap's floor of "
                f"{MIN_CLUSTERS}; the job would fail at the interval"
            )
    except SystemExit as refused:
        problems.append(f"{org}: {refused}")
    except Exception as e:
        problems.append(f"{org}: data path failed: {type(e).__name__}: {e}")

# The chat frame is the format the model is evaluated on; a template that renders to the
# raw prompt means the tokenizer shipped without one.
if tok is not None:
    sample = build_prompt({"comments": ["c"], "before": "x"})
    if render_chat(tok, sample) == sample:
        problems.append("tokenizer has no chat template; training and eval would be raw")


# 4. The checkout is the pushed commit: HEAD against the remote branch, not against itself.
def git(*git_args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO), *git_args], capture_output=True, text=True, timeout=60
        )
    except Exception:
        return None
    return result.stdout.strip() or None


branch = git("rev-parse", "--abbrev-ref", "HEAD")
checked_out = git("rev-parse", "HEAD")
pushed = None
if branch:
    listed = git("ls-remote", "origin", f"refs/heads/{branch}")
    pushed = listed.split()[0] if listed else None
if checked_out is None or pushed is None:
    problems.append(f"could not resolve HEAD ({checked_out}) or origin/{branch} ({pushed})")
else:
    # check_repo_matches speaks from the workstation's side: `local` is what was pushed,
    # `remote` is what this machine will run.
    problems += check_repo_matches(local=pushed, remote=checked_out)
if git("status", "--porcelain", "--untracked-files=no"):
    problems.append("the checkout has uncommitted changes; the job would run code with no SHA")

print()
if problems:
    print("PREFLIGHT FAILED")
    for p in problems:
        print(f"  - {p}")
    raise SystemExit(1)
print("PREFLIGHT OK - safe to sbatch")
