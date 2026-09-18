#!/bin/bash
#SBATCH --job-name=sphragis-geometry
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:30:00
#SBATCH --output=logs/sphragis-geometry-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# Pairwise cosines between every saved update, through r x r traces. CPU only: no GPU requested.
# PATTERN selects which adapters, so one client run's are never mixed with another's.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

PATTERN="${PATTERN:-sphragis-adapters-clients/*-c*/adapter_model.safetensors}"

uv run --no-sync python scripts/adapter_geometry.py \
  --root "$HOME/scratch" \
  --pattern "$PATTERN" \
  --per-module \
  --out "$HOME/client-geometry$RESULT_SUFFIX.json"
