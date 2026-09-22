.DEFAULT_GOAL := help
.PHONY: help sync lint fmt test test-cov gpu-local corpus-verify clean deploy submit submit-pinned cluster-env verify docs docs-serve docs-index docs-harvest pull-logs

# --no-sync throughout: plain `uv run` re-syncs the venv to the lockfile on every
# invocation, which silently removes the experiment extra (see `make gpu-local`).
# `make sync` is the one place the environment is meant to change.

help:                      ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

sync:                      ## Install dependencies
	uv sync

lint:                      ## ruff format check + ruff lint + ty type-check
	uv run --no-sync --no-active ruff format --check .
	uv run --no-sync --no-active ruff check .
	uv run --no-sync --no-active ty check

fmt:                       ## Apply ruff formatting + autofixes
	uv run --no-sync --no-active ruff format .
	uv run --no-sync --no-active ruff check --fix .

test:                      ## Run the test suite
	uv run --no-sync --no-active pytest

test-cov:                  ## Run the test suite with coverage
	uv run --no-sync --no-active pytest --cov=sphragis --cov-report=term-missing --cov-report=xml

gpu-local:                 ## Install the model stack for local GPU work (CUDA torch on any Linux)
	@# The lockfile resolves cu130 torch on every Linux machine, so this is the extra alone.
	uv sync --extra experiment
	@uv run --no-sync python -c "import torch, peft, transformers; \
	print('torch', torch.__version__, '| cuda', torch.cuda.is_available(), \
	'| peft', peft.__version__)"
	@echo 'Run with --no-sync from here: a plain `uv run` re-syncs without the extra.'

TIGRIS_HOST ?= tigris
TIGRIS_DIR  ?= ajsoftworks/sphragis
REMOTE      ?= origin
BRANCH      ?= $(shell git rev-parse --abbrev-ref HEAD)
CLUSTER     ?= tigris
SLURM_CLI    = uv run --no-sync --no-active python -m sphragis.experiment.slurm
# ACCOUNT= overrides the project account, which is defined once, in slurm.py.
SLURM_TARGET = --target $(CLUSTER) $(if $(ACCOUNT),--account $(ACCOUNT))
SSH_OPTS     = -o ConnectTimeout=20 -o ServerAliveInterval=10 -o ServerAliveCountMax=3

deploy:                    ## Put the cluster on this branch's pushed HEAD, by SHA
	@# A read-only deploy key makes the cluster a real clone, so deployment is a fetch to
	@# a named commit rather than a file copy. That matters beyond tidiness: the design
	@# requires every stage to record the git SHA that produced it, and a copy has no SHA.
	@# Three jobs have already failed on a hand-copied or stale file; `git status` on the
	@# cluster now answers "is this the code I think it is".
	@# A queued job runs whatever is in the checkout when it *starts*, not what was there
	@# when it was submitted. At low fairshare that gap is a day or more, so deploying over
	@# a pending job silently changes the code it runs and the result cannot be attributed
	@# to a SHA. Refuse, and make overriding it deliberate: `make deploy FORCE=1`. Both
	@# clusters run this one checkout, so the queue is read on all of them.
	@# `git diff-index` alone reports stat-dirty entries as modifications, so rewriting a
	@# tracked results file with identical bytes refuses the deploy with nothing to commit.
	@git update-index -q --refresh
	@git diff-index --quiet HEAD -- || { echo "commit first: the cluster deploys a SHA, not a working tree"; exit 1; }
	@test -n "$(FORCE)" || { \
	  q=$$(ssh -o ConnectTimeout=20 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
	        $(TIGRIS_HOST) 'squeue -M all -h -u $$USER -o "%i %T" && echo && echo QUEUE_OK'); \
	  case "$$q" in *QUEUE_OK*) ;; *) echo "could not read the queue on $(TIGRIS_HOST); not deploying"; exit 1;; esac; \
	  j=$$(echo "$$q" | grep -v -e '^QUEUE_OK$$' -e '^CLUSTER: ' | grep -c . ); \
	  test "$$j" -eq 0 || { \
	    here=$$(ssh $(SSH_OPTS) $(TIGRIS_HOST) 'cd $(TIGRIS_DIR) && git rev-parse HEAD') \
	      || { echo "could not read the cluster checkout; not deploying"; exit 1; }; \
	    diff=$$(git diff --name-status "$$here" HEAD) \
	      || { echo "the cluster is on $$here, which this clone does not have; not deploying"; exit 1; }; \
	    if printf '%s\n' "$$diff" | $(SLURM_CLI) inert; then \
	      echo "$$j job(s) queued; this deploy changes nothing they run:"; printf '%s\n' "$$diff"; \
	    else \
	      echo "$$j job(s) queued or running:"; echo "$$q" | grep -v '^QUEUE_OK$$'; \
	      echo "this deploy changes code they could run. Cancel them, wait, or: make deploy FORCE=1"; exit 1; \
	    fi; }; }
	git push -q $(REMOTE) HEAD
	ssh $(TIGRIS_HOST) 'cd $(TIGRIS_DIR) && git fetch -q origin && git checkout -q -B $(BRANCH) origin/$(BRANCH) && git --no-pager log --oneline -1'

submit:                    ## Submit scripts/JOB.sbatch to CLUSTER (tigris|sporc|sporc-h100) as ACCOUNT
	@# Scripts keep their TIGRIS #SBATCH lines. sbatch ranks command-line options above them,
	@# so changing cluster never edits a script. TIME= overrides --time, whose values were
	@# measured on a GH200; SBATCH_ARGS= passes the rest, e.g. --export=ALL,MODE=windows.
	@# SBATCH_ARGS passes through slurm.py, which refuses bare words and the options the
	@# target and account own, and puts it first: sbatch keeps an option's last value, and a
	@# bare word would end option parsing before the checked ones. The venv is checked by pyvenv.cfg:
	@# bin/python may resolve only on the compute node's machine. Both refusals would
	@# otherwise surface only after the queue wait.
	@test -n "$(JOB)" || { echo "usage: make submit JOB=rq1 [CLUSTER=sporc] [TIME=HH:MM:SS] [SBATCH_ARGS=...]"; exit 1; }
	@test -f scripts/$(JOB).sbatch || { echo "no scripts/$(JOB).sbatch"; exit 1; }
	@git update-index -q --refresh
	@git diff-index --quiet HEAD -- || { echo "commit and make deploy first: a job runs the cluster's checkout"; exit 1; }
	@flags=$$($(SLURM_CLI) flags $(SLURM_TARGET) $(if $(TIME),--time $(TIME)) --sbatch-args='$(SBATCH_ARGS)') || exit 1; \
	machine=$$($(SLURM_CLI) machine --target $(CLUSTER)) || exit 1; \
	head=$$(git rev-parse HEAD); \
	ssh $(TIGRIS_HOST) "cd $(TIGRIS_DIR) \
	  && { [ \"\$$(git rev-parse HEAD)\" = $$head ] || { echo 'the cluster is not on this commit: make deploy'; exit 1; }; } \
	  && { [ -f .venv-$$machine/pyvenv.cfg ] || { echo 'no .venv-$$machine on the cluster: make cluster-env CLUSTER=$(CLUSTER)'; exit 1; }; } \
	  && sbatch $$flags scripts/$(JOB).sbatch"

submit-pinned:             ## Submit scripts/JOB.sbatch from a worktree pinned at this pushed commit
	@# For running new code while jobs hold the main checkout. A deploy would change what they
	@# run and mislabel their results; a worktree at this commit changes neither, and its
	@# results record this commit. The venv is shared by symlink, and cluster-env.sh puts the
	@# worktree first on the import path. Worktrees accumulate under ~/sphragis-pinned.
	@test -n "$(JOB)" || { echo "usage: make submit-pinned JOB=rq1 [CLUSTER=...] [TIME=...] [SBATCH_ARGS=...]"; exit 1; }
	@test -f scripts/$(JOB).sbatch || { echo "no scripts/$(JOB).sbatch"; exit 1; }
	@git update-index -q --refresh
	@git diff-index --quiet HEAD -- || { echo "commit first: a pinned run is a commit, not a working tree"; exit 1; }
	@flags=$$($(SLURM_CLI) flags $(SLURM_TARGET) $(if $(TIME),--time $(TIME)) --sbatch-args='$(SBATCH_ARGS)') || exit 1; \
	machine=$$($(SLURM_CLI) machine --target $(CLUSTER)) || exit 1; \
	head=$$(git rev-parse HEAD); short=$$(git rev-parse --short HEAD); \
	git push -q $(REMOTE) HEAD && \
	ssh $(SSH_OPTS) $(TIGRIS_HOST) "cd $(TIGRIS_DIR) && git fetch -q origin \
	  && W=\$$HOME/sphragis-pinned/$$short \
	  && { [ -d \$$W ] || git worktree add -q --detach \$$W $$head; } \
	  && { [ \"\$$(git -C \$$W rev-parse HEAD)\" = $$head ] || { echo \"\$$W is not at $$head\"; exit 1; }; } \
	  && ln -sfn \$$HOME/$(TIGRIS_DIR)/.venv-$$machine \$$W/.venv-$$machine \
	  && { [ -f \$$W/.venv-$$machine/pyvenv.cfg ] || { echo 'no .venv-$$machine on the cluster: make cluster-env CLUSTER=$(CLUSTER)'; exit 1; }; } \
	  && cd \$$W && mkdir -p logs \
	  && SPHRAGIS_CHECKOUT=\$$W sbatch $$flags scripts/$(JOB).sbatch"

pull-logs:                 ## Copy the cluster's job logs into datasets/logs, the only other copy
	@# A result carries its provenance, but the run's stdout lives only on the cluster: the
	@# outcome-neutral lines, the printed contrasts, the warnings. They are a few kilobytes
	@# each and they are the record that a job ran as specified, so they are kept here rather
	@# than left on a filesystem this study does not control.
	@mkdir -p datasets/logs
	@# The count is the check, not the pipeline's exit code. An earlier form of this target
	@# flattened with --strip-components and extracted nothing, and the pipeline still exited
	@# 0, which is the shape of failure this repository treats as worse than a crash.
	@before=$$(ls datasets/logs/*.log 2>/dev/null | wc -l); \
	ssh $(SSH_OPTS) $(TIGRIS_HOST) 'find $$HOME/sphragis-pinned -name "sphragis-*.log" -print0 2>/dev/null | tar -czf - --null -T -' \
	  | tar -xzf - -C datasets/logs --transform 's#.*/##' 2>/dev/null; \
	after=$$(ls datasets/logs/*.log 2>/dev/null | wc -l); \
	if [ "$$after" -eq 0 ]; then \
	  echo "pull-logs copied nothing; is the cluster reachable and are there logs?" >&2; \
	  exit 1; \
	fi; \
	echo "datasets/logs holds $$after job log(s), $$((after - before)) new"

cluster-env:               ## Build uv + the venv for CLUSTER's machine type (aarch64 TIGRIS, x86_64 SPORC)
	@# The login node is aarch64 and builds its own venv in place. An x86_64 venv can only be
	@# built on an x86_64 machine, so for SPORC the same script runs as a CPU-only job there.
	@# Success is the builder's marker in its log: sbatch --wait --clusters=sporc exits 0 for
	@# a job cancelled while pending.
	@machine=$$($(SLURM_CLI) machine --target $(CLUSTER)) || exit 1; \
	flags=$$($(SLURM_CLI) flags $(SLURM_TARGET) --cpu-only --time 01:00:00) || exit 1; \
	ssh $(SSH_OPTS) $(TIGRIS_HOST) "cd $(TIGRIS_DIR) && version=\$$(uv --version | cut -d' ' -f2) \
	  && { [ -n \"\$$version\" ] || { echo 'no uv on the login node to pin the version from'; exit 1; }; } \
	  && if [ \"\$$(uname -m)\" = $$machine ]; then UV_VERSION=\$$version bash scripts/cluster_env.sh; \
	  else job=\$$(sbatch --parsable --wait $$flags --cpus-per-task=4 --mem=16G \
	    --job-name=sphragis-cluster-env --output=logs/sphragis-cluster-env-$(CLUSTER)-%j.log \
	    --export=ALL,UV_VERSION=\$$version scripts/cluster_env.sh); \
	    log=logs/sphragis-cluster-env-$(CLUSTER)-\$${job%%;*}.log; [ -n \"\$${job%%;*}\" ] && cat \$$log; \
	    grep -q CLUSTER_ENV_OK \$$log 2>/dev/null || { echo 'the build job did not finish: no CLUSTER_ENV_OK'; exit 1; }; fi"

docs:                      ## Build the documentation site into site/
	uv run --no-sync --no-active zensical build --clean

docs-serve:                ## Serve the documentation site with live reload
	uv run --no-sync --no-active zensical serve

docs-index:                ## Regenerate docs/artifacts.md from what the scripts declare they write
	@# STAGE NEW RESULTS FIRST. The index lists tracked artifacts, so running this before
	@# `git add` writes an index that omits exactly the files being added, and the commit is
	@# then refused by a staleness check that is correct. Caught twice on 2026-09-22.
	uv run --no-sync --no-active python harvest.py --index

docs-harvest:              ## Assert every figure on the site against its artifact
	@# Also fails when docs/artifacts.md is stale. The test suite runs the same check, so a
	@# page that has drifted from its measurement fails CI rather than waiting for a reader.
	uv run --no-sync --no-active python harvest.py --check

corpus-verify:             ## Re-derive the corpus manifest and fail on any mismatch
	uv run --no-active python -m sphragis.corpus verify

clean:                     ## Remove caches + build artifacts
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

verify:                    ## lint + test, reporting a bare exit code (no pipes to mask it)
	@$(MAKE) --no-print-directory lint > /dev/null 2>&1; echo "LINT_RC=$$?"
	@$(MAKE) --no-print-directory test > /dev/null 2>&1; echo "TEST_RC=$$?"
	@$(MAKE) --no-print-directory lint > /dev/null 2>&1 && $(MAKE) --no-print-directory test > /dev/null 2>&1 \
		&& echo "ALL GREEN" || { echo "NOT GREEN - run make lint / make test for detail"; exit 1; }
