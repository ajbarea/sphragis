#!/bin/bash
#SBATCH --job-name=sphragis-memory-probe
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=01:30:00
#SBATCH --output=logs/sphragis-memory-probe-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# Peak GPU memory of the registered 7B at the longest admissible item: two model loads for
# training (ranks 32 and 256, 2 optimiser steps at each of three lengths) and two for fp32
# inference. Written for SPORC's A100s (make submit-pinned JOB=memory_probe CLUSTER=sporc); the
# cluster suffix keeps one GPU's result from overwriting another's.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh
claim_result OUT "$SPHRAGIS_RESULTS/memory-probe$RESULT_SUFFIX.json"
uv run --no-sync python scripts/memory_probe.py --out "$OUT"
