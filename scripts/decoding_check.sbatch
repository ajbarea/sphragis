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
# The calibration run whose adapter and examples are reused, decoded in the dtype this run
# loads (decoding_check.py refuses another): the condition's fp32 run.
CALIBRATION="${CALIBRATION:-$CONDITION-fp32}"
TEMPERATURE="${TEMPERATURE:-1.0}"
# Named in every result. 1.0 is off; the checkpoint's generation config sets 1.1.
# REPETITION_PENALTY=1.0|1.1
repetition_penalty

claim_result OUT "$SPHRAGIS_RESULTS/decoding-$CALIBRATION-t$TEMPERATURE-rp$REPETITION_PENALTY$RESULT_SUFFIX.json"
uv run --no-sync python scripts/decoding_check.py \
  --condition "$CONDITION" \
  --results "$SPHRAGIS_RESULTS/calibration-$CALIBRATION.json" \
  --corpus-dir "$SPHRAGIS_DATA/corpus/planted" \
  --adapter "$SPHRAGIS_ADAPTERS/sphragis-adapters-$CALIBRATION/b-s1" \
  --temperature "$TEMPERATURE" \
  --repetition-penalty "$REPETITION_PENALTY" \
  --out "$OUT"
