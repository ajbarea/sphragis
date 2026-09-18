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
# minutes for 940 even with the fp32 pass slower. Submit once per node the stored runs used:
#
#   make submit JOB=determinism_check SBATCH_ARGS=--nodelist=gh-a-081
#   make submit JOB=determinism_check SBATCH_ARGS=--nodelist=gh-a-103
#
# The node is in the result name, so the two never overwrite each other.
set -euo pipefail

cd "$HOME/ajsoftworks/sphragis"
source sphragis/experiment/cluster-env.sh

CONDITION="${CONDITION:-sym-0}"

uv run --no-sync python scripts/determinism_check.py \
  --condition "$CONDITION" \
  --corpus-dir "$HOME/corpus/planted" \
  --stored "$HOME/calibration-$CONDITION.json" \
  --out "$HOME/determinism-$CONDITION-$SLURMD_NODENAME$RESULT_SUFFIX.json"
