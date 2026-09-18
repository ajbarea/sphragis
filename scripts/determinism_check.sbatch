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

# Where does greedy decoding's run-to-run variation come from? Six passes of the base model over
# the same 150 prompts, no training: about 900 generations, the size of the decoding check (57
# minutes), with the two fp32 passes slower than bf16.
#
# DETERMINISTIC=1 adds torch's deterministic algorithms, which need cuBLAS's workspace pinned
# before CUDA initialises.
set -euo pipefail

cd "$HOME/ajsoftworks/sphragis"
source sphragis/experiment/cluster-env.sh

CONDITION="${CONDITION:-sym-0}"
MODE=""
if [ "${DETERMINISTIC:-0}" = 1 ]; then
  export CUBLAS_WORKSPACE_CONFIG=:4096:8
  MODE="-deterministic"
fi

uv run --no-sync python scripts/determinism_check.py \
  --condition "$CONDITION" \
  --corpus-dir "$HOME/corpus/planted" \
  --stored "$HOME/calibration-marker-0.json" "$HOME/calibration-$CONDITION.json" \
  --out "$HOME/determinism-$CONDITION$MODE$RESULT_SUFFIX.json"
