"""Run every check the pilot depends on, on the login node, before queueing anything.

CPU only, no GPU allocation, no queue. Exercises the real import path and the real data
pipeline on real examples, and confirms the checkout is the pushed commit.

Every check here has to be able to fail. The previous version compared the checkout's SHA
to itself, required a hand-copied `$HOME/pilot.py` that the deploy discipline forbids, and
validated `TrainingArguments` keywords for a trainer the pilot no longer uses.
"""

import json
import subprocess
import sys
from pathlib import Path

from sphragis.experiment.preflight import check_paths_exist, check_repo_matches

REPO = Path(__file__).resolve().parents[1]
problems: list[str] = []

# 1. Inputs exist and are non-empty, and job output has somewhere to go. Slurm does not
# create the --output directory; a job that cannot open its log dies after the queue wait.
examples = Path(sys.argv[1] if len(sys.argv) > 1 else "pilot-examples.jsonl")
problems += check_paths_exist(
    {"examples": examples, "pilot script": REPO / "scripts" / "pilot.py", "logs": REPO / "logs"}
)

# 2. The imports the job will make actually resolve here.
try:
    from transformers import AutoTokenizer

    from sphragis.corpus.pipeline import run_dedup
    from sphragis.experiment.model import MODEL_ID, TRAINING
    from sphragis.experiment.runner import build_prompt
    from sphragis.experiment.training import build_supervised, render_chat
except Exception as error:
    problems.append(f"import failed: {type(error).__name__}: {error}")
    print("\n".join(problems))
    raise SystemExit(1) from error

# 3. The data path works on every real example, with the real tokenizer, and nothing is
# silently dropped. The pilot refuses items that exceed the budget; this reports how many.
try:
    rows = [json.loads(line) for line in examples.read_text().splitlines() if line]
    kept, removed = run_dedup(rows)
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    refused = 0
    for r in kept:
        try:
            item = build_supervised(
                tok, r, prompt_builder=build_prompt, max_length=TRAINING["max_seq_length"]
            )
        except ValueError:
            refused += 1
            continue
        assert len(item["input_ids"]) == len(item["labels"]) == len(item["attention_mask"])
        assert any(x != -100 for x in item["labels"]), "no supervised tokens"
    # The chat frame is the format the model is evaluated on; a template that renders to
    # the raw prompt means the tokenizer shipped without one.
    sample = build_prompt(kept[0])
    if render_chat(tok, sample) == sample:
        problems.append("tokenizer has no chat template; training and eval would be raw")
    print(f"data path ok: {len(kept)} after dedup {dict(removed)}, {refused} refused as too long")
    if refused:
        problems.append(f"{refused} examples exceed max_seq_length and would be dropped")
except Exception as e:
    problems.append(f"data path failed: {type(e).__name__}: {e}")


# 4. The checkout is the pushed commit: HEAD against the remote branch, not against itself.
def git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=60
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
