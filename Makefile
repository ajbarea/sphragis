.DEFAULT_GOAL := help
.PHONY: help sync lint fmt test test-cov corpus-verify clean

help:                      ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

sync:                      ## Install dependencies
	uv sync

lint:                      ## ruff format check + ruff lint + ty type-check
	uv run --no-active ruff format --check .
	uv run --no-active ruff check .
	uv run --no-active ty check

fmt:                       ## Apply ruff formatting + autofixes
	uv run --no-active ruff format .
	uv run --no-active ruff check --fix .

test:                      ## Run the test suite
	uv run --no-active pytest

test-cov:                  ## Run the test suite with coverage
	uv run --no-active pytest --cov=sphragis --cov-report=term-missing --cov-report=xml

corpus-verify:             ## Re-derive the corpus manifest and fail on any mismatch
	uv run --no-active python -m sphragis.corpus verify

clean:                     ## Remove caches + build artifacts
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
