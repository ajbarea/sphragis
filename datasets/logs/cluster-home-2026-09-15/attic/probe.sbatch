#!/bin/bash
#SBATCH --job-name=sphragis-gpu-probe
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=00:30:00
#SBATCH --output=$HOME/logs/sphragis-gpu-probe-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
export HF_HOME=$HOME/hf-cache
export TOKENIZERS_PARALLELISM=false

cd $HOME/ajsoftworks/sphragis && uv run python $HOME/probe.py