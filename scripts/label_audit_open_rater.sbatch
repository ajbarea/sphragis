#!/bin/bash
#SBATCH --job-name=sphragis-open-rater
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/sphragis-open-rater-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# The label audit's open-weight rater: 383 short generations (64 new tokens each) with Qwen3-14B
# in bf16 (28 GB, so a 40 GB A100 also fits); four hours is ample. ITEMS is the prepared items file, staged to the cluster by hand
# (default $SPHRAGIS_DATA/open-rater/items.jsonl) because it is corpus text and never committed.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh
claim_result OUT "$SPHRAGIS_RESULTS/label-audit-v2-open-rater-labels$RESULT_SUFFIX.json"
uv run --no-sync python scripts/label_audit_open_rater.py rate \
  --items "${ITEMS:-$SPHRAGIS_DATA/open-rater/items.jsonl}" --out "$OUT"
