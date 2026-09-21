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

# The default pattern gives the empty suffix, so existing names are unchanged.
name_result_after_adapters "$PATTERN"
# Another sketch width is another set of vectors, so it names its own file.
[ "${SKETCH:-16}" = 16 ] || RESULT_SUFFIX="$RESULT_SUFFIX-w$SKETCH"
export RESULT_SUFFIX

claim_result OUT "$HOME/client-vectors$RESULT_SUFFIX.npz"
uv run --no-sync python scripts/adapter_projection.py \
  --root "$HOME/scratch" \
  --pattern "$PATTERN" \
  --sketch "${SKETCH:-16}" \
  --out "$OUT"
