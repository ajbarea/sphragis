#!/bin/bash
#SBATCH --job-name=sphragis-retrieval-baseline
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=12:00:00
#SBATCH --output=logs/sphragis-retrieval-baseline-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# Few-shot retrieval against the adapters' contrasts: four prompts per development example (alone,
# and with three BM25 neighbours from the own half, the sibling half and the other organization),
# greedy, one model load. About 4,800 generations at the registered two organizations; twelve
# hours leaves room for slower GPUs. Exploratory, outside the registered cells.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh
ROOT="${CORPUS:-$SPHRAGIS_DATA/corpus-v3}"
claim_result OUT "$SPHRAGIS_RESULTS/retrieval-baseline$RESULT_SUFFIX.json"
uv run --no-sync python scripts/retrieval_baseline.py --root "$ROOT" \
  --orgs openstack wikimedia ${LIMIT:+--limit "$LIMIT"} --out "$OUT"
