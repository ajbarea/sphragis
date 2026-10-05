#!/bin/bash
#SBATCH --job-name=sphragis-power-at-size
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --cpus-per-task=64
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/sphragis-power-at-size-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# Power at the registered bound on a test window below its projected size ("Fetch horizon").
# CPU only: the partition simulation's trials at one lift per Holm level. Every input is read from
# the test report and the simulation it was read under.
#
# Walltime from the simulation's measured trial (partition_sensitivity.sbatch): about 43 s at
# 2,017 changes, so at most two lifts of 1,000 trials take about 25 minutes on 64 cores, plus the
# partition pool's rebuild; 4 hours allows for slower cores.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

ORG="${ORG:-openstack}"
claim_result OUT "$SPHRAGIS_RESULTS/power-at-size-$ORG$RESULT_SUFFIX.json"
uv run --no-sync python scripts/power_at_size.py \
  --report "${REPORT:?set REPORT to the test-window report of ORG}" \
  --simulation "datasets/results/partition-sensitivity-$ORG.json" \
  --placebo "${PLACEBO:?set PLACEBO to the placebo run the simulation used}" \
  --corpus "${CORPUS_WINDOWS:?set CORPUS_WINDOWS to the corpus root}" \
  --admissible "datasets/results/admissible-partitions-$ORG.json" \
  --workers "$(nproc)" \
  --out "$OUT"
