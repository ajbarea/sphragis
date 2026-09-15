.DEFAULT_GOAL := help
.PHONY: help sync lint fmt test test-cov corpus-verify clean deploy

# --no-sync throughout: plain `uv run` re-syncs the venv to the lockfile on every
# invocation, which silently reverts a locally installed CUDA torch (see `make gpu-local`).
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

gpu-local:                 ## Swap in a CUDA torch for local GPU work (x86_64 dev boxes)
	@# The lockfile pins CUDA torch only for linux/aarch64, which is TIGRIS. CI and this
	@# box are x86_64 and resolve the CPU wheel, and `uv run` re-syncs to that on every
	@# invocation. --reinstall-package is required: uv treats 2.14.0+cpu as already
	@# satisfying 2.14.0 and will not swap the variant otherwise.
	@# Order matters: sync the extra first (which installs the CPU torch), then swap the
	@# wheel. Doing it the other way round, or only swapping, leaves peft and transformers
	@# missing under --no-sync.
	uv sync --extra experiment
	uv pip install --reinstall-package torch --index-url https://download.pytorch.org/whl/cu130 torch
	@uv run --no-sync python -c "import torch, peft, transformers; \
	print('torch', torch.__version__, '| cuda', torch.cuda.is_available(), \
	'| peft', peft.__version__)"
	@echo 'Run with --no-sync from here, or `uv run` puts the CPU wheel back.'

TIGRIS_HOST ?= tigris
TIGRIS_DIR  ?= ajsoftworks/sphragis

deploy:                    ## rsync this checkout to the cluster (one source of truth)
	@# The cluster cannot clone this repo: it is private and TIGRIS has no GitHub
	@# credential. So deployment is a copy, and a copy drifts -- three jobs have already
	@# failed on a stale or missing file, including one on a module that existed only
	@# here. Syncing the WHOLE checkout, with --delete, is what makes that class impossible:
	@# never hand-copy an individual script into the cluster's $$HOME.
	@# Excluded dirs are protected from --delete; .venv on the cluster is the aarch64 one
	@# and must survive, and logs/ holds the cluster's job output, which exists only there.
	rsync -az --delete --exclude '.venv/' --exclude '.git/' --exclude '__pycache__/' \
		--exclude '.pytest_cache/' --exclude '.ruff_cache/' --exclude 'datasets/' \
		--exclude 'logs/' \
		./ $(TIGRIS_HOST):$(TIGRIS_DIR)/
	@echo "deployed to $(TIGRIS_HOST):$(TIGRIS_DIR)"

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
