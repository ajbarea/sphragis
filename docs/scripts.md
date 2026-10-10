---
title: Scripts
description: What each job and tool under scripts/ does, and the order the registered H1 reading runs in.
---

# Scripts

Every committed measurement and the script that wrote it is in the [artifact index](artifacts.md).
This page says how the scripts fit together. `.sbatch` files are Slurm jobs, submitted with
`make submit JOB=<name>` (the `.sbatch` suffix is left off); a job script and the Python file of
the same name are one tool.

## Reading H1

The registered H1 reading runs in five steps, per organization.

| step | script | what it does |
|---|---|---|
| 1 | `admissible_partitions.py` | Writes the ordered list of admissible partitions the runs are drawn from, fixed before any run. Run k uses the k-th entry. |
| 2 | `partition_run.sbatch` | One run of H1: the organization deduplicated once, its projects assigned in the partition seed's order, both halves trained at the fixed size and each scored on both halves. Takes `ORG`, `PARTITION_SEED`, `TRAIN_SIZE` and `SEEDS` from the admissible list. |
| 3 | `partition_pilot.py` | Reads H1 over the first K runs at the registered Holm levels. On the development window it is the pilot, and its per-run spread gives the reproducibility K. |
| 4 | `partition_sensitivity.sbatch` | Simulates H1 at a given K (`partition_sensitivity.py`, CPU only): coverage, detectable effect and reproducibility, or with `NULL_ONLY=1` the null alone, once per K on the coverage grid. Its bounds at the registered K feed `partition_pilot.py --sensitivity`. |
| 5 | `k_coverage.py` | Applies the K coverage rule to the pilot and the grid's null simulations and writes `k-coverage-<org>-likelihood.json`, the only K source a test read takes (`partition_pilot.py --sizing`). |

A test-window read takes its K, bounds and planted run from these artifacts, as
[Registered decisions](registered-decisions.md) describes. `reading.py` prints a result's
readings with their labels, and `power_at_size.sbatch` reads a test window that falls below its
projected size.

## Comparators

| script | what it does |
|---|---|
| `retrieval_comparator.sbatch` | Few-shot retrieval as the adapters' comparator, reading the corpora the partition runs read. |
| `rules_distil.sbatch` | Distils a rules file from each half's reviews, or reads an organization's written conventions page by page. |
| `rules_guides.py` | Snapshots each organization's written coding conventions, pinned, for the rules-file comparator. |

## Corpus collection

| script | what it does |
|---|---|
| `fetch_chromium.sh` | Collects Chromium over the train and development windows through the CLI, over git. |
| `control_window.sh` | Collects the pre-cutoff control window, built exactly like the post-cutoff window. |
| `resume_when_allowed.sh` | Finishes a month's build once the host is answering again. |
| `extract_bot_templates.py` | Extracts Qt's Sanity Bot message templates from the hook that writes them. |
| `contamination_windows.py` | Assembles the two sides of the contamination battery, matched month for month. |

## Label audit

| script | what it does |
|---|---|
| `label_audit_sample.py` | Draws a stratified sample of training examples for auditing whether each label is real. |
| `label_audit_blind.py` | Blinds an audit sheet for its raters: neutral ids, a fixed random order, no organization. |
| `label_audit_page.py` | Builds the page a human uses to check one rater's labels blind. |
| `label_audit_decision.py` | Runs a decision model as a third blind rater, through Ollama. |
| `label_audit_open_rater.sbatch` | Runs the open-weight rater at pinned weights. |

## Cluster operations

| script | what it does |
|---|---|
| `cluster_env.sh` | Builds uv and the venv for the machine it runs on; `make cluster-env` runs it. |
| `wait_for_job.sh` | Waits for a Slurm job to leave the queue, then prints its accounting and log tail. |
| `preflight_pilot.py` | Runs every check a pilot job depends on, on the login node, before queueing anything. |
| `bench_throughput.py` | Measures 7B throughput on a GH200, with compilation separated from steady state. |
| `memory_probe.sbatch` | Measures peak GPU memory of the registered 7B at the longest admissible item. |

## Organization-level RQ1 and instrument checks

These predate the H1 and H2 decomposition and stay as the record the registered choices rest on.

| script | what it does |
|---|---|
| `rq1.sbatch` | The OpenStack against Qt contrast, in `MODE=pilot` or `MODE=windows`. |
| `pilot.sbatch` | The pilot: whether an adapted 7B clears the exact-match floor on this corpus. |
| `placebo_gate.sbatch` | The gate run against two halves of one organization. |
| `project_contrast.sbatch` | The same contrast between two projects inside one organization. |
| `project_corpora.py` | Builds those two projects' corpora, one per side. |
| `calibration.sbatch` | The weakest house style the contrast can see, by planting conventions of known strength. |
| `estimands.py` | What the estimand choice costs, on a run that has already happened. |
| `decoding_check.sbatch` | Re-evaluates a saved calibration adapter at a chosen temperature and repetition penalty, to test whether the decoder amplifies a planted convention. |
| `decoder_check.sbatch` | Rescores a stored H1 partition run at an explicit repetition penalty, its adapters reused, so the decoder is the only change. |
| `determinism_check.sbatch` | Four passes of the base model over the same prompts, to locate run-to-run variation in greedy decoding. |
| `prompt_format_probe.sbatch` | Measures how much of the pilot's base-model floor is the prompt format. |
| `contamination_battery.sbatch` | The contamination battery, run on the windows `contamination_windows.py` assembles. |

## RQ2 client updates

| script | what it does |
|---|---|
| `client_updates.sbatch` | RQ2's client updates: one small training run per client, saved for the geometry and defence analyses. |
| `dual_adapter_updates.sbatch` | The adapter-instance cut: two adapters a client, only the global one communicated. |
| `fdlora_schedule.sbatch` | FDLoRA's schedule (Lu et al., arXiv:2406.07925): a global module seeded from the personalized ones. |
| `adapter_geometry.sbatch` | Pairwise cosines between every saved update. |
| `adapter_projection.sbatch` | Sketches every client update to a short vector, so a defence can be simulated. |

## Site and tests

| script | what it does |
|---|---|
| `log_to_blog.py` | Publishes the research log as a dated journal, one post per entry, at build time. |
| `prune_site.py` | Removes every built page the nav does not name. |
| `jgit_fixtures.py` | Derives `tests/fixtures/jgit_edits.json` from JGit itself. |
