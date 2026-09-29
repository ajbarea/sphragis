#!/bin/bash
#SBATCH --job-name=sphragis-partition-sensitivity
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --cpus-per-task=64
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/sphragis-partition-sensitivity-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# H1 over repeated partitions, simulated: coverage, detectable effect, stopping and
# reproducibility. CPU only, pure Python in a process pool: one worker per allocated core.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

ORG="${ORG:-openstack}"
claim_result OUT "$SPHRAGIS_RESULTS/partition-sensitivity-$ORG$RESULT_SUFFIX.json"
uv run --no-sync python scripts/partition_sensitivity.py \
  --placebo "${PLACEBO:?set PLACEBO to a single-seed placebo run}" \
  --corpus "${CORPUS_WINDOWS:?set CORPUS_WINDOWS to the corpus root}" --org "$ORG" \
  --size "${SIZE:?set SIZE to the planned test changes}" \
  --sigma-run ${SIGMA_RUN:-0.011 0.015 0.025 0.035} \
  --null-trials "${NULL_TRIALS:-1000}" --trials "${TRIALS:-300}" \
  --workers "$(nproc)" \
  --out "$OUT"
