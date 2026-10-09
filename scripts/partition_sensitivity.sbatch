#!/bin/bash
#SBATCH --job-name=sphragis-partition-sensitivity
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --cpus-per-task=64
#SBATCH --mem=64G
#SBATCH --time=36:00:00
#SBATCH --output=logs/sphragis-partition-sensitivity-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# H1 over repeated partitions at the registered K, simulated: coverage, detectable effect and
# reproducibility. CPU only, pure Python in a process pool: one worker per allocated core. Every
# input is read from the committed artifact that fixed it (K and the run spread from the pilot,
# the planned test size from the test-window projection, N and the reference from the
# admissible list).
#
# Walltime from a measured trial, not a guess: about 43 s a trial at 2,017 changes, K = 24 and
# 10,000 resamples (128 trials on 8 local cores, 2026-09-30). A full run simulates about 16 lifts
# of 1,000 trials and 4,000 null trials at each of four spread points, some 80,000 trials: about
# 15 hours on 64 cores, so 36 allows for slower cores.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

ORG="${ORG:-openstack}"
# PILOT: the pilot artifact K and the run spread come from, the organization's own by default;
# a rerun under another decoder names its pilot and takes a RUN_TAG for its result.
# METRIC: exact_match, or logprob_per_token with a likelihood pilot; the result is named for it.
METRIC="${METRIC:-exact_match}"
[ "$METRIC" = exact_match ] || RESULT_SUFFIX="$RESULT_SUFFIX-$METRIC"
# SMOKE=1: a few trials end to end before the full run, under a result name of its own.
case "${SMOKE:-0}" in
  1) SMOKE_ARGS=(--trials 20 --null-trials 40 --calibration-trials 4 --steps 3); RESULT_SUFFIX="$RESULT_SUFFIX-smoke" ;;
  0) SMOKE_ARGS=() ;;
  *) echo "SMOKE must be 0 or 1, not $SMOKE" >&2; exit 1 ;;
esac
claim_result OUT "$SPHRAGIS_RESULTS/partition-sensitivity-$ORG$RESULT_SUFFIX.json"
uv run --no-sync python scripts/partition_sensitivity.py \
  --placebo "${PLACEBO:?set PLACEBO to a single-seed placebo run}" \
  --corpus "${CORPUS_WINDOWS:?set CORPUS_WINDOWS to the corpus root}" --org "$ORG" \
  --pilot "${PILOT:-datasets/results/partition-pilot-$ORG.json}" \
  --projection "datasets/results/project-windows-$ORG-v3.json" \
  --admissible "datasets/results/admissible-partitions-$ORG.json" \
  --metric "$METRIC" ${SMOKE_ARGS[@]+"${SMOKE_ARGS[@]}"} \
  --workers "$(nproc)" \
  --out "$OUT"
