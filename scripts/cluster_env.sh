#!/bin/bash
# Build uv and the venv for the machine this runs on. `make cluster-env` runs it on the TIGRIS
# login node when the target is aarch64, and as a CPU job on the target cluster otherwise, since
# an x86_64 venv can only be built on an x86_64 machine.
#
# UV_VERSION pins the uv installed where none runs yet; make passes the login node's, so both
# machines resolve the lockfile with one uv.
set -euo pipefail
cd "$HOME/ajsoftworks/sphragis"
SPHRAGIS_BUILDING_ENV=1 source sphragis/experiment/cluster-env.sh

if ! uv --version >/dev/null 2>&1; then
  : "${UV_VERSION:?no uv runs on $machine; set UV_VERSION to install one}"
  archive="uv-$machine-unknown-linux-gnu.tar.gz"
  release="https://github.com/astral-sh/uv/releases/download/$UV_VERSION"
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  curl -fsSL -o "$work/$archive" "$release/$archive"
  curl -fsSL -o "$work/$archive.sha256" "$release/$archive.sha256"
  (cd "$work" && sha256sum -c "$archive.sha256")
  mkdir -p "$HOME/.local/bin/$machine"
  tar -xzf "$work/$archive" -C "$work"
  install -m 755 "$work/uv-$machine-unknown-linux-gnu/uv" "$work/uv-$machine-unknown-linux-gnu/uvx" \
    "$HOME/.local/bin/$machine/"
fi
uv --version

uv sync --frozen --extra experiment
uv run --no-sync python -c "import torch, transformers, peft; \
print('torch', torch.__version__, '| cuda build', torch.version.cuda, '| peft', peft.__version__)"
echo "CLUSTER_ENV_OK $UV_PROJECT_ENVIRONMENT"
