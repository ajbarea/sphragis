# skill-context — sphragis

Repo-specific facts the techne skills read. Logic lives in the skills; only facts belong here.

## repo

- name: sphragis
- kind: research apparatus (corpus, measurement, experiment) for a pre-registered MSR 2027
  study, with a Zensical docs site. Private until the Stage 1 submission.
- default_branch: main
- package_root: `sphragis/` with three subpackages: `corpus/` (fetch, scrub, dedup, split,
  manifest, seal), `measure/` (score, stats, contamination, attribution, probe),
  `experiment/` (model, training, grid, runner, slurm). `scripts/` holds the analyses and
  cluster jobs that write every published number; `harvest.py` checks the site against them.
- language: Python (>=3.12,<3.15); CI matrix covers 3.12, 3.13, 3.14
- toolchain: uv (canonical, `--no-sync --no-active` in every make target), ruff (format +
  lint), ty (types), pytest
- cli_entrypoint: `uv run python -m sphragis.corpus {build,verify,...}`
- runner: none. Targets run directly via `make`; there is no `logs/dev-<ts>-*.log` archive
  convention. `logs/` holds Slurm job output instead, so the audit's log-reconciliation
  phase is N/A.
- has: an `experiment` extra with the GPU model stack (installed by `make gpu-local` or
  `make cluster-env`), RIT Research Computing cluster jobs (`scripts/*.sbatch`, submitted
  with `make submit CLUSTER=<tigris|sporc|sporc-h100>`), no Docker, no frontend.

## audit

### Phase 1 — Setup

- `make sync` → `uv sync`

### Phase 2 — Lint

- `make lint` → `ruff format --check .`, `ruff check .`, `ty check`

### Phase 3 — Test

- `make test` → pytest. The suite includes the study invariants and the site-harvest check,
  so a drifted docs figure or a loosened invariant fails here, not only in its own target.
- `make verify` → lint + test, printing `LINT_RC=` / `TEST_RC=` and a final
  `ALL GREEN` / `NOT GREEN`. Read those lines, not a piped exit code.

### Phase 4 — Artifact checks

- `make docs-harvest` → asserts every figure on the site against its artifact
- `make redact-check` → reports third-party addresses in the result artifacts
- `make corpus-verify` → re-derives the corpus manifest; needs the raw snapshots under
  `datasets/gerrit/*/raw/`, which are gitignored, so it fails on a fresh clone

### Fast audit

- `make verify`

### do_not_run

- `make submit`, `make submit-pinned`, `make deploy`, `make cluster-env`, `make pull-logs`:
  cluster jobs and remote state
- `make gpu-local`: installs the CUDA model stack
- `make docs-serve`: interactive server
- `make fmt`, `make redact`: rewrite files in place

## ci_audit

- workflows: `.github/workflows/ci.yml` (Lint (ruff + ty), Test on 3.12 / 3.13 / 3.14),
  `.github/workflows/docs.yml` (harvest check, Zensical build, Pages deploy),
  `.github/workflows/dependabot-auto-merge.yml`
- required checks on `main`: `Lint (ruff + ty)` and the three `Test (Python …)` legs
- referenced configs: `pyproject.toml` (`requires-python`, `[tool.ruff]`, dev group),
  `uv.lock` (CI runs `uv lock --check` then `uv sync --frozen`), `Makefile`, `zensical.toml`
- tool error markers: `ruff`, `ty`, `FAILED` / `passed` (pytest), `harvest` (a site figure
  that disagrees with its artifact), `uv lock --check` (lockfile out of sync)

## slop_ground_truth

- Every quantitative claim traces to an artifact under `datasets/results/` and is recorded
  once, in `docs/research-log.md`. `harvest.py --check` enforces the site side; anything
  else stating a number states status and points at the log.
- The study invariants are deliberate and test-asserted, not style: nothing under
  `sphragis/measure/` or `sphragis/experiment/` imports a GPU stack except
  `experiment/model.py` (the `*_purity` tests), a seal without in-principle acceptance stays
  locked (`test_cli.py`), and exact match is byte identity (`test_score.py`). Comments
  explaining any of these are load-bearing.
- Long comments in `Makefile` recipes and `scripts/` record a failure that happened once
  (a Slurm job that died at IO setup, an index written before `git add`). Keep them.

## scan_scope

Skip paths:

- `.venv/`, `.venv-*/`, `site/`, `logs/`, `datasets/`, `__pycache__/`, `.ruff_cache/`,
  `.pytest_cache/`, `.cache/`, `uv.lock`, `*.whl`

Subagent scan-area split:

- Library: `sphragis/**/*.py`
- Analyses and cluster jobs: `scripts/**`, `harvest.py`
- Tests: `tests/**/*.py`
- Config / build: `pyproject.toml`, `Makefile`, `.github/workflows/**`, `zensical.toml`
- Docs: `docs/**/*.md`, `README.md` (only when asked; the research log is a record, not prose
  to tighten)

## docs_site

- config: `zensical.toml`
- workflow: `.github/workflows/docs.yml`
- css_files: `docs/stylesheets/extra.css`
- js_files: none
- build_command: `uv run --no-sync --no-active zensical build --clean`
- site_url: `https://ajbarea.github.io/sphragis/`
- action_pins: full-SHA pins with a `# vX.Y.Z` comment, kept current by Dependabot
- figures: every number on a page is checked against its artifact by `make docs-harvest`,
  which `docs.yml` runs before building
