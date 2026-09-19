#!/bin/bash
#SBATCH --job-name=sphragis-decoding
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=01:30:00
#SBATCH --output=logs/sphragis-decoding-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# Is greedy decoding what amplifies a planted convention? Re-evaluates a saved calibration
# adapter with sampling on the same held-out examples, so no training: one model load and
# about 940 generations, roughly 15 minutes at the measured 0.85 s/generation, with room for
# sampling running longer than greedy when it declines to stop.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

CONDITION="${CONDITION:-marker-0.25}"
TEMPERATURE="${TEMPERATURE:-1.0}"

claim_result OUT "$HOME/decoding-$CONDITION-t$TEMPERATURE$RESULT_SUFFIX.json"
uv run --no-sync python scripts/decoding_check.py \
  --condition "$CONDITION" \
  --results "$HOME/calibration-$CONDITION.json" \
  --corpus-dir "$HOME/corpus/planted" \
  --adapter "$HOME/scratch/sphragis-adapters-$CONDITION/b-s1" \
  --temperature "$TEMPERATURE" \
  --out "$OUT"
