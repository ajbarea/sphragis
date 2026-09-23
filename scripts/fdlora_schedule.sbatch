#!/bin/bash
#SBATCH --job-name=sphragis-fdlora
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=12:00:00
#SBATCH --output=logs/sphragis-fdlora-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# FDLoRA's schedule: a global module seeded from the average of every client's personalized
# module, then K inner steps a round with the personalized module overwritten every H rounds.
# ROUNDS=6, INNER_STEPS=3 (the paper's own default) and SYNC_PERIOD=5 land between the paper's
# own tested endpoints (H in {1, 3, 5, 10, T, infinity}): H=1 saves an identical transmitted and
# withheld adapter by construction (the deliberate synchronous endpoint, not a first-run default),
# H > ROUNDS never syncs at all, and H=3 with ROUNDS=6 syncs on the final round too -- the same
# construction as H=1, by coincidence rather than intent -- which is why the script itself refuses
# any ROUNDS/SYNC_PERIOD pair where the last round syncs unless ALLOW_FINAL_SYNC=1 says so. H=5
# is the paper's own tested value that does not divide 6, so the withheld half is read one round
# after its last sync rather than the round it was just overwritten in. Stage 1 plus six rounds of
# three-epoch InnerOpt a client is a little over twice dual_adapter_updates' feddpa-t2 budget (that
# run took 90 minutes at this client size), so twelve hours holds with margin.
#
# The H sweep this job is one point of needs at least two packing draws a point (PACKING_SEED),
# the same rule client_updates.sbatch already follows (docs/research-log.md, 2026-09-18, "256
# examples a client is not a third point on this corpus"): a single packing cannot tell a real
# effect of H from noise in which examples landed on which client.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh
tag_result_suffix CLIENT_SIZE

ROUNDS="${ROUNDS:-6}"
INNER_STEPS="${INNER_STEPS:-3}"
SYNC_PERIOD="${SYNC_PERIOD:-5}"
# Every one of the three changes what is measured (round-0 average aside, this is what decides
# how much the withheld and transmitted halves converge), so each combination names its own
# result rather than sharing a default name that a later sweep would silently overwrite.
RESULT_SUFFIX="$RESULT_SUFFIX-r$ROUNDS-k$INNER_STEPS-h$SYNC_PERIOD"
export RESULT_SUFFIX

C="${CLIENTS:-$HOME/corpus/clients3}"
SOURCES="${SOURCES:-scripts/clients-cpp-rebuilt.txt}"

claim_result OUT "$HOME/client-updates-fdlora$RESULT_SUFFIX.json"
uv run --no-sync python scripts/fdlora_schedule.py \
  --clients "${CLIENTS_PER_SOURCE:-6}" \
  --client-size "${CLIENT_SIZE:-64}" \
  --rounds "$ROUNDS" \
  --inner-steps "$INNER_STEPS" \
  --sync-period "$SYNC_PERIOD" \
  ${ALLOW_FINAL_SYNC:+--allow-final-sync} \
  ${PACKING_SEED:+--packing-seed "$PACKING_SEED"} \
  --sources-file "$SOURCES" \
  --corpus-root "$C" \
  --adapters "$HOME/scratch/sphragis-adapters-fdlora$RESULT_SUFFIX" \
  --out "$OUT"
