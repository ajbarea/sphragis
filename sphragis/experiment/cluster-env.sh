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
# LEGACY_CORPUS=1 lets a job read corpus files cut before the current label rules, to reproduce an
# earlier result, and the result records it. Any other value, 0 included, leaves the check on.
legacy_corpus_flag() { if [ "${LEGACY_CORPUS:-0}" = 1 ]; then echo --legacy-corpus; fi; }

# A recorded measurement is written once. `claim_result NAME PATH` creates PATH exclusively and
# sets NAME to it, so a result that already exists, or that a concurrent job has claimed, stops
# this job before its work starts. The check runs when the job starts, after the queue wait. The
# claim is an empty file that the job's script later fills, beside a `.claim` naming the job that
# made it; a job that exits without filling it releases both, so a crash does not block the rerun,
# and `_abandoned_claim` reads the record when a kill left no chance to. OVERWRITE=1 replaces a
# result deliberately and claims nothing. Call it as a plain command in the job's own shell,
# never inside $(...), or the release on exit belongs to a subshell that has already gone.
# Only on the first source: a second one in the same shell would drop the claims already made,
# and they would then survive an exit that should have released them.
declare -p SPHRAGIS_CLAIMS >/dev/null 2>&1 || SPHRAGIS_CLAIMS=()
_release_unfilled_claims() {
  local path
  for path in ${SPHRAGIS_CLAIMS[@]+"${SPHRAGIS_CLAIMS[@]}"}; do
    [ -s "$path" ] || rm -f -- "$path"
    rm -f -- "$path.claim"
  done
}
# Bash runs an EXIT trap when a fatal signal takes it, so scancel's SIGTERM and the time limit
# release the claim. SIGKILL cannot, and the claim it strands is what `_abandoned_claim` reads.
trap _release_unfilled_claims EXIT
# The portal's job list shows the job name, and every job of one kind used to carry the same one:
# three "sphragis-geometry" rows say nothing about which run each belongs to. The result a job
# claims is exactly what distinguishes it, so the name takes the result's basename. Renaming is
# best effort: a Slurm that refuses it, or a run outside Slurm, must not fail the job over a label.
name_job_after_result() {
  local path="$1" base
  [ -n "${SLURM_JOB_ID:-}" ] || return 0
  command -v scontrol >/dev/null 2>&1 || return 0
  base="$(basename "$path")"
  base="${base%%.*}"
  scontrol update "JobId=$SLURM_JOB_ID" "JobName=$base" >/dev/null 2>&1 || true
}

# A killed job leaves an empty claim that looks exactly like one a running job has just made, and
# a requeue of that same job then refuses its own leftover. The claim records who made it, so the
# two can be told apart: an empty claim is abandoned when its owner is this job (a requeue keeps
# the id) or when Slurm no longer lists that job. A claim with a result in it is never abandoned,
# and an empty claim whose owner cannot be read is left alone, since nothing says it is dead.
#
# The owner is a cluster and an id, never an id alone. TIGRIS and SPORC are separate Slurm
# installations over one $HOME, so their job-id counters run independently: an id can name a live
# job on the other cluster, and `squeue` answers only for the local one, reporting a foreign id as
# unknown, which reads as "gone". Both branches below therefore require the cluster to match, and
# a claim from the other cluster is refused rather than guessed at.
_abandoned_claim() {
  local path="$1" owner owner_cluster owner_id here listing held
  [ -e "$path" ] && [ ! -s "$path" ] || return 1
  # A claim this run already holds is live by definition. Without this, two calls naming one path
  # in a single job would read the second as a requeue of the first and let it through, where the
  # exclusive create used to catch the duplicate.
  for held in ${SPHRAGIS_CLAIMS[@]+"${SPHRAGIS_CLAIMS[@]}"}; do
    [ "$held" != "$path" ] || return 1
  done
  # No record of an owner, so nothing says the claim is dead: an empty file is also what a
  # running job's claim looks like before it writes.
  owner="$(cat -- "$path.claim" 2>/dev/null)"
  [ -n "$owner" ] || return 1
  case "$owner" in
    # A record from before the cluster was written names no cluster, so it identifies nobody.
    *:*) ;;
    *) return 1 ;;
  esac
  owner_cluster="${owner%%:*}"
  owner_id="${owner#*:}"
  here="${SLURM_CLUSTER_NAME:-none}"
  if [ "$owner_cluster" != "$here" ] || [ "$here" = none ]; then
    echo "$path was claimed on $owner_cluster by job $owner_id, and this runs on $here:" \
      "pass OVERWRITE=1 if that job is known to be dead" >&2
    return 1
  fi
  if [ "$owner_id" = "${SLURM_JOB_ID:-}" ]; then
    echo "$path was claimed by this job and left empty: reclaiming it" >&2
    return 0
  fi
  command -v squeue >/dev/null 2>&1 || return 1
  # A squeue that cannot answer is not evidence. Only an empty listing for a job it recognises
  # says the owner has left; an unknown id, which is what a long-finished job becomes, refuses.
  listing="$(squeue -h -j "$owner_id" 2>/dev/null)" || return 1
  [ -z "$listing" ] || return 1
  echo "$path was claimed by job $owner_id on $owner_cluster, which is no longer queued:" \
    "reclaiming it" >&2
  return 0
}

# Two jobs read client adapters through a PATTERN and name their output after the adapters they
# read rather than after RUN_TAG: the pattern is what selects the input, and a run over one client
# size that inherited another's suffix would overwrite the geometry every committed RQ2 number was
# computed from. One definition, because the two jobs must agree on the name.
name_result_after_adapters() {
  local pattern="$1" first selector shape count_word has_local has_p0
  local -a shapes matched
  # The name comes from the pattern's first directory, so that directory must be literal: a glob
  # there would name the output after nothing.
  first="${pattern%%/*}"
  case "$first" in
    *[\*\?\[]*) echo "PATTERN's first directory must be literal, got $first" >&2; return 1 ;;
  esac
  # `sphragis-adapters-clients-X` gives `-X`, which is what every committed result is named after;
  # any other `sphragis-adapters-Y` gives `-Y`, so the dual-adapter runs name themselves without
  # colliding with the single-adapter ones.
  case "$first" in
    sphragis-adapters-clients*) RESULT_SUFFIX="${first#sphragis-adapters-clients}" ;;
    sphragis-adapters-*) RESULT_SUFFIX="-${first#sphragis-adapters-}" ;;
    *) echo "PATTERN must start with a sphragis-adapters directory, got $first" >&2; return 1 ;;
  esac
  # A dual-adapter run saves more than one adapter a client in one directory: the transmitted one
  # under the client's own name, and one or more withheld ones beside it (FedDPA's `-local`;
  # FDLoRA's `-local` and `-p0`, the round-0 average of the personalized modules). Each shape is
  # its own geometry over one set of adapters, so a selector has to name exactly one apart, and
  # one that spans more than one is refused rather than written to whichever name it inherits
  # first. Only where a withheld shape exists at all: a single-adapter directory keeps the names
  # it already has.
  compgen -G "$HOME/scratch/$first/*-local" >/dev/null 2>&1 && has_local=1 || has_local=0
  compgen -G "$HOME/scratch/$first/*-p0" >/dev/null 2>&1 && has_p0=1 || has_p0=0
  if [ "$has_local" = 0 ] && [ "$has_p0" = 0 ]; then export RESULT_SUFFIX; return 0; fi
  selector="${pattern#*/}"
  selector="${selector%%/*}"
  # Only the shapes actually present in this directory are candidates: a directory with just
  # FedDPA's `-local` must still be refused only for spanning those two, not FDLoRA's `-p0` too.
  shapes=(a-c0)
  [ "$has_local" = 0 ] || shapes+=(a-c0-local)
  [ "$has_p0" = 0 ] || shapes+=(a-c0-p0)
  matched=()
  for shape in "${shapes[@]}"; do
    case "$shape" in $selector) matched+=("$shape") ;; esac
  done
  if [ "${#matched[@]}" -gt 1 ]; then
    case "${#matched[@]}" in
      2) count_word=two ;;
      3) count_word=three ;;
      *) count_word="${#matched[@]}" ;;
    esac
    echo "PATTERN selects more than one of $first's adapter shapes together" \
      "(${matched[*]}), and they are $count_word geometries: select one, with */*-local/*," \
      "*/*-p0/* or a selector that cannot match them" >&2
    return 1
  fi
  case "$selector" in
    *-local) RESULT_SUFFIX="$RESULT_SUFFIX-local" ;;
    *-p0) RESULT_SUFFIX="$RESULT_SUFFIX-p0" ;;
  esac
  export RESULT_SUFFIX
}

claim_result() {
  local name="$1" path="$2" why claimed=0
  if [ "${OVERWRITE:-0}" != 1 ]; then
    if why="$( (set -o noclobber; : >"$path") 2>&1)"; then
      claimed=1
    elif _abandoned_claim "$path"; then
      rm -f -- "$path" "$path.claim"
      why="$( (set -o noclobber; : >"$path") 2>&1)" && claimed=1
    fi
    if [ "$claimed" != 1 ]; then
      # A missing directory or a permission fails the same exclusive create, and must not be
      # reported as a clash with a job that does not exist.
      if [ -e "$path" ]; then
        echo "$path exists or another job has claimed it: pass OVERWRITE=1 to replace it deliberately" >&2
      else
        echo "cannot claim $path: $why" >&2
      fi
      return 1
    fi
    SPHRAGIS_CLAIMS+=("$path")
    # The cluster belongs in the record: an id alone is ambiguous across the two installations.
    [ -z "${SLURM_JOB_ID:-}" ] ||
      printf '%s:%s\n' "${SLURM_CLUSTER_NAME:-unknown}" "$SLURM_JOB_ID" >"$path.claim" 2>/dev/null ||
      true
  fi
  name_job_after_result "$path"
  printf -v "$name" '%s' "$path"
}

# Another seed set or training size is another run, not a rerun, so it is named whatever
# RUN_TAG says: SEEDS=2 alone would otherwise overwrite the seed-1 result and its adapters.
# A script calls this with the variables it passes on, so one it ignores never renames it.
tag_result_suffix() {
  local name
  for name in "$@"; do
    case "$name" in
      SEEDS) [ "${SEEDS:-1}" = 1 ] || RESULT_SUFFIX="$RESULT_SUFFIX-s${SEEDS//,/-}" ;;
      TRAIN_SIZE) [ -z "${TRAIN_SIZE:-}" ] || RESULT_SUFFIX="$RESULT_SUFFIX-n$TRAIN_SIZE" ;;
      CLIENT_SIZE) [ "${CLIENT_SIZE:-64}" = 64 ] || RESULT_SUFFIX="$RESULT_SUFFIX-c$CLIENT_SIZE" ;;
      LORA_RANK) [ "${LORA_RANK:-32}" = 32 ] || RESULT_SUFFIX="$RESULT_SUFFIX-r$LORA_RANK" ;;
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
