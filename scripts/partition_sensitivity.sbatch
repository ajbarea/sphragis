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

# H1 over repeated partitions at the registered K, simulated: coverage, detectable effect and
# reproducibility. CPU only, pure Python in a process pool: one worker per allocated core. Every
# input is read from the committed artifact that fixed it (K and the run spread from the pilot,
# the planned test size from the test-window projection, N and the reference from the
# admissible list).
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

ORG="${ORG:-openstack}"
claim_result OUT "$SPHRAGIS_RESULTS/partition-sensitivity-$ORG$RESULT_SUFFIX.json"
uv run --no-sync python scripts/partition_sensitivity.py \
  --placebo "${PLACEBO:?set PLACEBO to a single-seed placebo run}" \
  --corpus "${CORPUS_WINDOWS:?set CORPUS_WINDOWS to the corpus root}" --org "$ORG" \
  --pilot "datasets/results/partition-pilot-$ORG.json" \
  --projection "datasets/results/project-windows-$ORG-v3.json" \
  --admissible "datasets/results/admissible-partitions-$ORG.json" \
  --workers "$(nproc)" \
  --out "$OUT"
