#!/bin/bash
#SBATCH --job-name=sphragis-determinism
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=01:30:00
#SBATCH --output=logs/sphragis-determinism-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# Where does greedy decoding's run-to-run variation come from? Four passes of the base model
# over the same 150 prompts, no training: 600 generations, under the decoding check's 57
# minutes for 940 even with the fp32 pass slower. Submit once on each of two nodes, those the
# registered fp32 measurement used:
#
#   make submit JOB=determinism_check SBATCH_ARGS=--nodelist=gh-a-081
#   make submit JOB=determinism_check SBATCH_ARGS=--nodelist=gh-a-003
#
# The node is in the result name, so the two never overwrite each other.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

CONDITION="${CONDITION:-sym-0}"

repetition_penalty
RESULT_SUFFIX="$RESULT_SUFFIX-rp$REPETITION_PENALTY"
claim_result OUT "$SPHRAGIS_RESULTS/determinism-$CONDITION-$SLURMD_NODENAME$RESULT_SUFFIX.json"
uv run --no-sync python scripts/determinism_check.py \
  --condition "$CONDITION" \
  --corpus-dir "$SPHRAGIS_DATA/corpus/planted" \
  --stored "$SPHRAGIS_RESULTS/calibration-$CONDITION.json" \
  --repetition-penalty "$REPETITION_PENALTY" \
  --out "$OUT"
