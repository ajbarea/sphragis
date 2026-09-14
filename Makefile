.DEFAULT_GOAL := help
.PHONY: help sync lint fmt test test-cov corpus-verify clean

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

corpus-verify:             ## Re-derive the corpus manifest and fail on any mismatch
	uv run --no-active python -m sphragis.corpus verify

clean:                     ## Remove caches + build artifacts
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
