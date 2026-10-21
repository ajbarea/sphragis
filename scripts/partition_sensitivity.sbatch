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

# A cell over repeated partitions at the registered K, simulated: coverage, detectable effect and
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
# FOREIGN_SOURCE: H2's cell against that source, simulated from its own pilot
# (partition_pilot.py --hypothesis H2), under a pilot and result name of its own.
FOREIGN_TAG="${FOREIGN_SOURCE:+-foreign$FOREIGN_SOURCE}"
RESULT_SUFFIX="$RESULT_SUFFIX$FOREIGN_TAG"
FOREIGN_ARGS=(${FOREIGN_SOURCE:+--foreign-source "$FOREIGN_SOURCE"})
if [ -n "${FOREIGN_SOURCE:-}" ] && [ "$METRIC" != logprob_per_token ]; then
  echo "FOREIGN_SOURCE: H2 is simulated on METRIC=logprob_per_token, not $METRIC" >&2; exit 1
fi
case "$METRIC" in
  exact_match) PILOT_TAG="" ;;
  logprob_per_token) PILOT_TAG="-likelihood"; RESULT_SUFFIX="$RESULT_SUFFIX-$METRIC" ;;
  *) echo "METRIC must be exact_match or logprob_per_token, not $METRIC" >&2; exit 1 ;;
esac
# CHAINS=1: relation chains as the drawn unit (research log, 2026-10-21), from the organization's
# chain artifact and its pilot read under chains, under a result name of its own.
CHAIN_ARGS=()
case "${CHAINS:-0}" in
  1) CHAIN_ARGS=(--chains "datasets/results/relation-chains-$ORG.json"); PILOT_TAG="$PILOT_TAG-chains"
     RESULT_SUFFIX="$RESULT_SUFFIX-chains" ;;
  0) ;;
  *) echo "CHAINS must be 0 or 1, not $CHAINS" >&2; exit 1 ;;
esac
# RUNS=<K> and NULL_ONLY=1: the coverage search over K, the null's rates at a K in place of the
# pilot's sizing, each under a result name of its own.
COVERAGE_ARGS=()
if [ -n "${RUNS:-}" ]; then COVERAGE_ARGS+=(--runs "$RUNS"); RESULT_SUFFIX="$RESULT_SUFFIX-k$RUNS"; fi
case "${NULL_ONLY:-0}" in
  1) COVERAGE_ARGS+=(--null-only); RESULT_SUFFIX="$RESULT_SUFFIX-null" ;;
  0) ;;
  *) echo "NULL_ONLY must be 0 or 1, not $NULL_ONLY" >&2; exit 1 ;;
esac
# SMOKE=1: a few trials end to end before the full run, under a result name of its own.
case "${SMOKE:-0}" in
  1) SMOKE_ARGS=(--trials 20 --null-trials 40 --calibration-trials 4 --steps 3); RESULT_SUFFIX="$RESULT_SUFFIX-smoke" ;;
  0) SMOKE_ARGS=() ;;
  *) echo "SMOKE must be 0 or 1, not $SMOKE" >&2; exit 1 ;;
esac
# PLACEBO: the single-seed placebo run exact match resamples; a continuous metric reads none.
PLACEBO_ARGS=()
if [ -n "${PLACEBO:-}" ]; then PLACEBO_ARGS=(--placebo "$PLACEBO")
elif [ "$METRIC" = exact_match ]; then echo "set PLACEBO to a single-seed placebo run" >&2; exit 1; fi
claim_result OUT "$SPHRAGIS_RESULTS/partition-sensitivity-$ORG$RESULT_SUFFIX.json"
uv run --no-sync python scripts/partition_sensitivity.py \
  ${PLACEBO_ARGS[@]+"${PLACEBO_ARGS[@]}"} \
  --corpus "${CORPUS_WINDOWS:?set CORPUS_WINDOWS to the corpus root}" --org "$ORG" \
  --pilot "${PILOT:-datasets/results/partition-pilot-$ORG$FOREIGN_TAG$PILOT_TAG.json}" \
  ${FOREIGN_ARGS[@]+"${FOREIGN_ARGS[@]}"} \
  --projection "datasets/results/project-windows-$ORG-v3.json" \
  --admissible "datasets/results/admissible-partitions-$ORG.json" \
  --metric "$METRIC" ${SMOKE_ARGS[@]+"${SMOKE_ARGS[@]}"} \
  ${CHAIN_ARGS[@]+"${CHAIN_ARGS[@]}"} \
  ${COVERAGE_ARGS[@]+"${COVERAGE_ARGS[@]}"} \
  --workers "$(nproc)" \
  --out "$OUT"
