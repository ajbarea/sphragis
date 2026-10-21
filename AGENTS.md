# sphragis

Sphragis is the corpus, measurement and experiment code behind a pre-registered study of whether an organization's house style is learnable from the code it reviews. Humans start with [README.md](README.md).

## Commands

`make help` lists every target. Run these before a PR:

```bash
make sync     # uv sync
make verify   # lint + test; read its LINT_RC=, TEST_RC= and ALL GREEN / NOT GREEN lines
```

`make docs-harvest` checks every figure on the docs site against its artifact. The cluster targets (`submit`, `submit-pinned`, `deploy`, `cluster-env`, `pull-logs`), `gpu-local` and the in-place rewriters `fmt` and `redact` change remote or local state: run them only when the task says to.

## Rules

- Work in a git worktree and open a pull request; `main` is protected.
- Never commit `COMMITS.md` (a local scratchpad).
- Every number traces to an artifact under `datasets/results/` and is recorded once, in `docs/research-log.md`. Quote figures through `scripts/reading.py`; elsewhere state status and point at the log.
- Do not hand-edit `docs/artifacts.md` (`make docs-index` writes it) or `docs/log/` (generated from the research log).
- Corpus text, generation runs of written-grant hosts and `datasets/gerrit*/` raw snapshots stay out of git; `.gitignore` lists them. Publish model text only through `scripts/withhold_text.py`.
- The study invariants are tested on purpose: only `experiment/model.py` imports a GPU stack under `sphragis/measure/` or `sphragis/experiment/`, a seal without in-principle acceptance stays locked, and exact match is byte identity. Do not loosen a test to make a change pass.
- Comments in `Makefile` recipes and `scripts/` record a failure that happened once. Keep them.

## State

`IMPL.md` holds the current slice and next step; `ROADMAP.md` the long view.
`.claude/skill-context.md` holds the facts the techne skills read for this repo.
