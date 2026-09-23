#!/bin/bash
#SBATCH --job-name=sphragis-fmt
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=00:25:00
#SBATCH --output=logs/sphragis-fmt-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

set -euo pipefail

# From the deployed checkout, never a hand-copied script in $HOME. `make deploy` is the
# only way code reaches this machine.
cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh
claim_result OUT "$HOME/prompt-format-probe$RESULT_SUFFIX.json"
uv run --no-sync python scripts/prompt_format_probe.py \
  "$HOME/pilot-examples.jsonl" \
  $(legacy_corpus_flag) \
  --out "$OUT"
