# The environment of every cluster job. Sourced from the checkout root by scripts/*.sbatch and
# inlined by sphragis.experiment.slurm.render, so there is one copy.
#
# TIGRIS is aarch64 and SPORC x86_64, and both mount the same $HOME: uv and the venv are kept
# per machine, and the checks below fail in seconds rather than after the model has loaded.
machine="$(uname -m)"
export PATH="$HOME/.local/bin/$machine:$HOME/.local/bin:$PATH"
export UV_PROJECT_ENVIRONMENT=".venv-$machine"
# The checkout this job entered comes first on the import path. The venv's editable install
# points at the main checkout, so a job run from a pinned worktree (`make submit-pinned`) would
# otherwise import the main checkout's code under the worktree's commit.
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
# One interpreter on every machine, the one the GH200 results so far ran on. Unpinned, each
# machine used whatever uv found or downloaded first.
export UV_PYTHON=3.13.15
# HF_HUB_CACHE, not HF_HOME: relocating HF_HOME also relocates the auto-refreshing OAuth token
# `hf auth login` writes, and the job would then authenticate with a stale copy.
export HF_HUB_CACHE=$HOME/hf-cache/hub
export TOKENIZERS_PARALLELISM=false
# The commit this job starts on, which sphragis.provenance records in place of whatever the
# checkout holds when the result is written. Empty outside a checkout.
SPHRAGIS_GIT_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
SPHRAGIS_GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
export SPHRAGIS_GIT_COMMIT SPHRAGIS_GIT_BRANCH
# Appended to every result and adapter path a job writes. Empty on TIGRIS, so existing names
# hold; elsewhere the cluster name, so a run there cannot overwrite a GH200 result by omission.
# RUN_TAG names it instead, and an explicitly empty RUN_TAG overwrites deliberately.
if [ -z "${RUN_TAG+set}" ]; then
  RUN_TAG="${SLURM_CLUSTER_NAME:-tigris}"
  [ "$RUN_TAG" != tigris ] || RUN_TAG=""
fi
export RESULT_SUFFIX="${RUN_TAG:+-$RUN_TAG}"
# Another seed set or training size is another run, not a rerun, so it is named whatever
# RUN_TAG says: SEEDS=2 alone would otherwise overwrite the seed-1 result and its adapters.
# A script calls this with the variables it passes on, so one it ignores never renames it.
tag_result_suffix() {
  local name
  for name in "$@"; do
    case "$name" in
      SEEDS) [ "${SEEDS:-1}" = 1 ] || RESULT_SUFFIX="$RESULT_SUFFIX-s${SEEDS//,/-}" ;;
      TRAIN_SIZE) [ -z "${TRAIN_SIZE:-}" ] || RESULT_SUFFIX="$RESULT_SUFFIX-n$TRAIN_SIZE" ;;
      *) echo "tag_result_suffix: no tag for $name" >&2; return 1 ;;
    esac
  done
  export RESULT_SUFFIX
}
# RIT hides the system gcc behind /tools/bin/blindfold/gcc, which refuses to run. Triton reads
# CC before `which gcc` when it JIT-compiles on first generation.
export CC=/usr/bin/gcc
# scripts/cluster_env.sh sets SPHRAGIS_BUILDING_ENV to create what these checks require.
if [ -z "${SPHRAGIS_BUILDING_ENV:-}" ]; then
  if ! uv --version >/dev/null 2>&1; then
    echo "no uv that runs on $machine; run: make cluster-env CLUSTER=<target>" >&2
    exit 1
  fi
  if [ ! -x "$UV_PROJECT_ENVIRONMENT/bin/python" ]; then
    echo "no $UV_PROJECT_ENVIRONMENT in $PWD; run: make cluster-env CLUSTER=<target>" >&2
    exit 1
  fi
fi
