#!/bin/bash
#SBATCH --job-name=sphragis-pilot
#SBATCH --account=rc-onboard
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=00:45:00
#SBATCH --output=logs/sphragis-pilot-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
# HF_HUB_CACHE, not HF_HOME: relocating HF_HOME also relocates the auto-refreshing OAuth
# token `hf auth login` writes, and the job would then authenticate with a stale copy.
export HF_HUB_CACHE=$HOME/hf-cache/hub
export TOKENIZERS_PARALLELISM=false
export CC=/usr/bin/gcc

# From the deployed checkout, never a hand-copied script in $HOME. `make deploy` is the
# only way code reaches this machine.
cd "$HOME/ajsoftworks/sphragis"
uv run --no-sync python scripts/pilot.py \
  "$HOME/pilot-examples.jsonl" \
  --out "$HOME/pilot-outcomes.json"
