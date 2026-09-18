#!/bin/bash
#SBATCH --job-name=sphragis-clients
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=02:00:00
#SBATCH --output=logs/sphragis-clients-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# RQ2's client updates: 34 clients of 64 examples over eight projects, one base-model load, 8
# optimiser steps each at the measured ~7 s a step, so about 45 minutes with saving; two hours
# covers a slower node. Build the corpora with scripts/project_corpora.py into $CLIENTS.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

C="${CLIENTS:-$HOME/corpus/clients}"

SOURCES="${SOURCES:-scripts/clients-cpp.txt}"

uv run --no-sync python scripts/client_updates.py \
  --clients 6 \
  --sources-file "$SOURCES" \
  --corpus-root "$C" \
  --adapters "$HOME/scratch/sphragis-adapters-clients$RESULT_SUFFIX" \
  --out "$HOME/client-updates$RESULT_SUFFIX.json"
