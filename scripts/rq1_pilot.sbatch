#!/bin/bash
#SBATCH --job-name=sphragis-rq1-pilot
#SBATCH --account=rc-onboard
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=00:45:00
#SBATCH --output=logs/sphragis-rq1-pilot-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# --time from measurement: job 143899 ran the full 2 by 2 grid plus base in 16m44s on a GH200
# (two adapters, 422 and 145 examples, 480 generations). A 2-hour request could not be
# scheduled ahead of a maintenance reservation that a 50-minute one fit before. Resize when
# the corpus or seed count grows.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
# HF_HUB_CACHE, not HF_HOME: relocating HF_HOME also relocates the auto-refreshing OAuth
# token `hf auth login` writes.
export HF_HUB_CACHE=$HOME/hf-cache/hub
export TOKENIZERS_PARALLELISM=false
export CC=/usr/bin/gcc

# Submit from the checkout: --output is relative and is opened before this body runs.
cd "$HOME/ajsoftworks/sphragis"
uv run --no-sync python scripts/rq1_pilot.py \
  --corpus "openstack=$HOME/corpus/openstack-2024-10.jsonl" \
  --corpus "qt=$HOME/corpus/qt-2024-10.jsonl" \
  --seeds 1 \
  --equalize-train \
  --adapters "$HOME/scratch/sphragis-adapters" \
  --out "$HOME/rq1-pilot-equalized.json"
