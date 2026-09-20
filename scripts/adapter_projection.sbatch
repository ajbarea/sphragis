#!/bin/bash
#SBATCH --job-name=sphragis-projection
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=logs/sphragis-projection-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# Sketch every client update to a short vector, so a defence can be simulated in a space that
# has directions to perturb. CPU only: two thin products a module, no GPU requested.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

PATTERN="${PATTERN:-sphragis-adapters-clients/*-c*/adapter_model.safetensors}"

# The output is named after the adapters it reads, not after this job's RUN_TAG: PATTERN is what
# selects the input, and a run over one client size that inherited another's suffix would
# overwrite the geometry every committed RQ2 number was computed from. The default pattern gives
# the empty suffix, so existing names are unchanged.
# The name is derived from PATTERN's first directory, so that directory must be a literal
# client-adapters directory: a glob or another prefix there would name the output after nothing.
FIRST="${PATTERN%%/*}"
case "$FIRST" in
  *[\*\?\[]*) echo "PATTERN's first directory must be literal, got $FIRST" >&2; exit 1 ;;
esac
# The suffix is derived from the adapters read, so two runs over different adapters can never
# share an output name. `sphragis-adapters-clients-X` gives `-X`, which is what every committed
# result is named after; any other `sphragis-adapters-Y` gives `-Y`, so the dual-adapter runs
# name themselves without colliding with the single-adapter ones.
case "$FIRST" in
  sphragis-adapters-clients*) RESULT_SUFFIX="${FIRST#sphragis-adapters-clients}" ;;
  sphragis-adapters-*) RESULT_SUFFIX="-${FIRST#sphragis-adapters-}" ;;
  *) echo "PATTERN must start with a sphragis-adapters directory, got $FIRST" >&2; exit 1 ;;
esac
# Another sketch width is another set of vectors, so it names its own file.
[ "${SKETCH:-16}" = 16 ] || RESULT_SUFFIX="$RESULT_SUFFIX-w$SKETCH"
export RESULT_SUFFIX

claim_result OUT "$HOME/client-vectors$RESULT_SUFFIX.npz"
uv run --no-sync python scripts/adapter_projection.py \
  --root "$HOME/scratch" \
  --pattern "$PATTERN" \
  --sketch "${SKETCH:-16}" \
  --out "$OUT"
