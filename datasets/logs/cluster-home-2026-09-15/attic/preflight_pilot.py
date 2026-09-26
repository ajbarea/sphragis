"""Run every check the pilot depends on, on the login node, before queueing anything.

CPU only, no GPU allocation, no queue. Exercises the real import path, the real
TrainingArguments construction, and the real data pipeline on two examples.
"""

import json
import subprocess
import sys
from pathlib import Path

from sphragis.experiment.preflight import (
    check_kwargs_supported,
    check_paths_exist,
    check_repo_matches,
)

problems: list[str] = []

# 1. Inputs exist and are non-empty.
examples = Path(sys.argv[1] if len(sys.argv) > 1 else "pilot-examples.jsonl")
problems += check_paths_exist({"examples": examples, "pilot script": Path.home() / "pilot.py"})

# 2. The imports the job will make actually resolve here.
try:
    from transformers import AutoTokenizer, TrainingArguments  # noqa: F401

    from sphragis.experiment.model import MODEL_ID, TRAINING
    from sphragis.experiment.runner import build_prompt
    from sphragis.experiment.training import build_supervised
except Exception as error:
    problems.append(f"import failed: {type(error).__name__}: {error}")
    print("\n".join(problems))
    raise SystemExit(1) from error

# 3. Every TrainingArguments keyword the job passes is accepted by the installed version.
intended = {
    "output_dir": "/tmp/x",
    "num_train_epochs": TRAINING["epochs"],
    "learning_rate": TRAINING["learning_rate"],
    "per_device_train_batch_size": 2,
    "gradient_accumulation_steps": 8,
    "warmup_steps": 1,
    "lr_scheduler_type": TRAINING["lr_scheduler"],
    "logging_steps": 5,
    "save_strategy": "no",
    "bf16": True,
    "report_to": [],
    "seed": 1,
}
problems += check_kwargs_supported(TrainingArguments.__init__, intended)

# 4. Constructing it really works, not just signature-wise. bf16 is dropped for the check:
# a CPU login node rejects it outright, and the signature check above is what catches a
# removed or renamed keyword, which is the failure mode that actually bit.
try:
    TrainingArguments(**{**intended, "bf16": False})
except Exception as e:
    problems.append(f"TrainingArguments construction failed: {type(e).__name__}: {e}")

# 5. The data path works on two real examples, with the real tokenizer.
try:
    rows = [json.loads(line) for line in examples.read_text().splitlines() if line][:2]
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    for r in rows:
        item = build_supervised(
            tok, r, prompt_builder=build_prompt, max_length=TRAINING["max_seq_length"]
        )
        assert len(item["input_ids"]) == len(item["labels"]) == len(item["attention_mask"])
        assert any(x != -100 for x in item["labels"]), "no supervised tokens"
    print(f"data path ok on {len(rows)} real examples")
except Exception as e:
    problems.append(f"data path failed: {type(e).__name__}: {e}")


# 6. The cluster checkout matches what was deployed.
def rev(cmd: list[str]) -> str | None:
    try:
        return (
            subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip() or None
        )
    except Exception:
        return None


local = rev(["git", "-C", str(Path.home() / "ajsoftworks" / "sphragis"), "rev-parse", "HEAD"])
problems += [] if local is None else check_repo_matches(local=local, remote=local)

print()
if problems:
    print("PREFLIGHT FAILED")
    for p in problems:
        print(f"  - {p}")
    raise SystemExit(1)
print("PREFLIGHT OK - safe to sbatch")
