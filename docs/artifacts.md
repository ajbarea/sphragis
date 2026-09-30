# Artifact index

Every measurement this study rests on is a committed file under
`datasets/results/`, and every one of them was written by a script in this
repository. This page is generated from those scripts: 212 artifacts under
45 writers, grouped by the script that wrote them, each described by
that script's own summary line. Run `make docs-index` to rebuild it, and the test
suite fails if it is stale.

A run that varies a knob names its output after the knob, so the variants of one
measurement sit together: a cluster suffix keeps an off-TIGRIS run from overwriting
a GH200 result, and `RUN_TAG`, `SEEDS`, `TRAIN_SIZE` and `CLIENT_SIZE` name a run
that is a different study rather than a repeat of the same one.

## `scripts/adapter_geometry.py`

How alike are two LoRA updates, and is it the data or the initialization that decides?

| artifact | |
|---|---|
| `adapter-geometry-s1.json` | `client-geometry-cpp-256-c256.json` |
| `client-geometry-cpp-early-a.json` | `client-geometry-cpp-early-b.json` |
| `client-geometry-cpp-early-c128-p2.json` | `client-geometry-cpp-early-c128.json` |
| `client-geometry-cpp-early.json` | `client-geometry-cpp-rebuilt-c128-a.json` |
| `client-geometry-cpp-rebuilt-c128-b.json` | `client-geometry-cpp-rebuilt-c128.json` |
| `client-geometry-dual-cpp-rebuilt-c128-t2-local.json` | `client-geometry-dual-cpp-rebuilt-c128-t2.json` |
| `client-geometry.json` |  |

## `scripts/adapter_projection.py`

Each client update as a short vector, so defences can be simulated honestly.

| artifact | |
|---|---|
| `client-vectors-cpp-256-c256.npz` | `client-vectors-cpp-early-c128-p2.npz` |
| `client-vectors-cpp-early-c128.npz` | `client-vectors-cpp-early.npz` |
| `client-vectors-cpp-rebuilt-c128.npz` | `client-vectors.npz` |

## `scripts/admissible_partitions.py`

The ordered list of admissible partitions H1's runs are drawn from, fixed before any run.

| artifact | |
|---|---|
| `admissible-partitions-openstack.json` |  |

## `scripts/aggregate_attack.py`

RQ2 under secure aggregation: does a round's aggregate betray whose clients were in it?

| artifact | |
|---|---|
| `aggregate-attack-cpp-256-c256.json` | `aggregate-attack-cpp-a.json` |
| `aggregate-attack-cpp-b.json` | `aggregate-attack-cpp-beyond-project-c128-p2.json` |
| `aggregate-attack-cpp-beyond-project-c128.json` | `aggregate-attack-cpp-beyond-project.json` |
| `aggregate-attack-cpp-early.json` | `aggregate-attack-cpp-only.json` |
| `aggregate-attack-cpp-rebuilt-c128-a.json` | `aggregate-attack-cpp-rebuilt-c128-b.json` |
| `aggregate-attack-cpp-rebuilt-c128.json` | `aggregate-attack-dual-cpp-rebuilt-c128-t2-local.json` |
| `aggregate-attack-dual-cpp-rebuilt-c128-t2.json` | `aggregate-attack-project.json` |
| `aggregate-attack.json` |  |

## `scripts/bot_sensitivity.py`

The registered dev-window reading with examples that rest only on automated reviewers removed.

| artifact | |
|---|---|
| `bot-sensitivity-placebo-openstack.json` | `bot-sensitivity-placebo-qt.json` |
| `bot-sensitivity-qtfull-fp32.json` | `bot-sensitivity-r256.json` |

## `scripts/censoring.py`

How much of each window's true cohort the collection boundary removes.

| artifact | |
|---|---|
| `censoring.json` |  |

## `scripts/client_attribution.py`

Read the client updates' geometry as a source-attribution attack, at three altitudes.

| artifact | |
|---|---|
| `client-attribution-cpp-256-c256.json` | `client-attribution-cpp-early-c128-p2.json` |
| `client-attribution-cpp-early-c128.json` | `client-attribution-cpp-early.json` |
| `client-attribution-cpp-rebuilt-c128.json` | `client-attribution-dual-cpp-rebuilt-c128-t2-local.json` |
| `client-attribution-dual-cpp-rebuilt-c128-t2.json` | `client-attribution.json` |

## `scripts/client_updates.py`

Federated client updates for RQ2: many small adapters, one initialization, several sources.

| artifact | |
|---|---|
| `client-updates-cpp-256-c256.json` | `client-updates-cpp-early-c128-p2.json` |
| `client-updates-cpp-early-c128.json` | `client-updates-cpp-early.json` |
| `client-updates-cpp-rebuilt-c128.json` | `client-updates.json` |

## `scripts/cluster_informativeness.py`

Is a change's size informative about its outcome?

| artifact | |
|---|---|
| `cluster-informativeness.json` |  |

## `scripts/conjunctive_power.py`

The power the RQ1 gate actually has, which is not the power that was computed.

| artifact | |
|---|---|
| `conjunctive-power-s10-b0.013.json` | `conjunctive-power-s3-b0.005.json` |
| `conjunctive-power-s3-b0.01.json` | `conjunctive-power-s3-b0.json` |
| `conjunctive-power-s5-b0.005.json` | `conjunctive-power-s5-b0.01.json` |
| `conjunctive-power-s5-b0.013.json` | `conjunctive-power-s5-b0.json` |
| `conjunctive-power-s7-b0.013.json` | `conjunctive-power.json` |

## `scripts/contamination_battery.py`

Outcome-neutral test 1 on real data: is the post-cutoff corpus less familiar to the model

| artifact | |
|---|---|
| `contamination-openstack-6mo-with_context-gapk.json` | `contamination-openstack-6mo-with_context.json` |
| `contamination-openstack-6mo-with_context.partial.json` | `contamination-openstack-after.json` |
| `contamination-openstack-with_context.json` |  |

## `scripts/crossed_coverage.py`

How often does each interval exclude zero when there is nothing to find?

| artifact | |
|---|---|
| `crossed-coverage.json` |  |

## `scripts/crossed_reread.py`

Re-read single-seed runs as one multi-seed study, under both gates.

| artifact | |
|---|---|
| `rq1-qtfull-seeds.json` |  |

## `scripts/data_audit.py`

A repeatable data audit of a built and refined corpus, one artifact per organization.

| artifact | |
|---|---|
| `data-audit-aosp.json` | `data-audit-openstack.json` |
| `data-audit-qt.json` |  |

## `scripts/decoding_check.py`

Is greedy decoding what amplifies a planted convention?

| artifact | |
|---|---|
| `decoding-marker-0.25-t1.0.json` |  |

## `scripts/decomposition_pilot.py`

The registered decomposition gate, read on development-window runs as a pilot.

| artifact | |
|---|---|
| `decomposition-pilot-openstack-v2.json` | `decomposition-pilot-openstack-v3.json` |

## `scripts/decomposition_sensitivity.py`

The smallest half-split effect the decomposition gate detects, and how often a null is bounded.

| artifact | |
|---|---|
| `decomposition-sensitivity-pooled.json` | `decomposition-sensitivity-v2.json` |
| `decomposition-sensitivity-v3-b0.0376.json` | `decomposition-sensitivity-v3.json` |
| `decomposition-sensitivity.json` |  |

## `scripts/defence_curve.py`

What masking an update with noise costs the attacker, and what it does not cost.

| artifact | |
|---|---|
| `defence-curve-cpp-early.json` | `defence-curve-cpp-only.json` |
| `defence-curve-cpp-rebuilt-c128-mid.json` | `defence-curve.json` |

## `scripts/determinism_check.py`

Where does greedy decoding's run-to-run variation come from?

| artifact | |
|---|---|
| `determinism-sym-0-gh-a-003.json` | `determinism-sym-0-gh-a-081.json` |

## `scripts/dual_adapter_updates.py`

The adapter-instance cut: two adapters per client, and only one of them leaves.

| artifact | |
|---|---|
| `client-updates-dual-cpp-rebuilt-c128-t2.json` |  |

## `scripts/host_scoping.py`

Which projects on a candidate Gerrit host could carry an organization, before collecting it.

| artifact | |
|---|---|
| `host-scoping-chromium.json` |  |

## `scripts/interval_calibration.py`

False-positive rate of the registered pairs cluster bootstrap, under a true null.

| artifact | |
|---|---|
| `interval-calibration-change-averaged.json` | `interval-calibration.json` |

## `scripts/label_audit_agreement.py`

Agreement on the label audit: two blind model raters, and a human's blind check of one.

| artifact | |
|---|---|
| `label-audit-v2.json` |  |

## `scripts/masking_mechanism.py`

Why does masking an update with noise make the attacker *better*?

| artifact | |
|---|---|
| `masking-mechanism.json` |  |

## `scripts/module_split.py`

Does the organization survive in the modules a selective scheme actually transmits?

| artifact | |
|---|---|
| `module-split-cpp-rebuilt-c128.json` | `module-split.json` |

## `scripts/notedb_parity.py`

The NoteDb git route against the REST-built AOSP corpus: enumeration, fields and examples.

| artifact | |
|---|---|
| `notedb-parity-aosp.json` |  |

## `scripts/partition_pilot.py`

H1 over repeated partitions on real runs: the cell, the number of runs, and the runs' order.

| artifact | |
|---|---|
| `partition-pilot-openstack-k24.json` | `partition-pilot-openstack-stopping.json` |
| `partition-pilot-openstack.json` |  |

## `scripts/partition_sensitivity.py`

Operating characteristics of H1 over repeated partitions at the registered K, by simulation.

| artifact | |
|---|---|
| `partition-sensitivity-openstack-stopping.json` | `partition-sensitivity-openstack.json` |

## `scripts/partition_variance.py`

How much of H1's reading is the partition: alternative balanced splits against the seed effect.

| artifact | |
|---|---|
| `partition-variance-openstack-v3.json` |  |

## `scripts/pilot.py`

Pilot: does an ADAPTED 7B clear the exact-match floor on this corpus?

| artifact | |
|---|---|
| `pilot-outcomes.json` |  |

## `scripts/placebo_corpus.py`

Two pseudo-organizations built from one organization's own projects.

| artifact | |
|---|---|
| `rq1-partition-openstack-p10-s8-n1850.json` | `rq1-partition-openstack-p11-s9-n1850.json` |
| `rq1-partition-openstack-p12-s10-n1850.json` | `rq1-partition-openstack-p14-s11-n1850.json` |
| `rq1-partition-openstack-p15-s12-n1850.json` | `rq1-partition-openstack-p17-s13-n1850.json` |
| `rq1-partition-openstack-p18-s14-n1850.json` | `rq1-partition-openstack-p19-s15-n1850.json` |
| `rq1-partition-openstack-p2-n1850.json` | `rq1-partition-openstack-p21-s16-n1850.json` |
| `rq1-partition-openstack-p22-s17-n1850.json` | `rq1-partition-openstack-p23-s18-n1850.json` |
| `rq1-partition-openstack-p24-s19-n1850.json` | `rq1-partition-openstack-p25-s20-n1850.json` |
| `rq1-partition-openstack-p26-s21-n1850.json` | `rq1-partition-openstack-p27-s22-n1850.json` |
| `rq1-partition-openstack-p28-s23-n1850.json` | `rq1-partition-openstack-p3-s2-n1850.json` |
| `rq1-partition-openstack-p30-s24-n1850.json` | `rq1-partition-openstack-p4-s3-n1850.json` |
| `rq1-partition-openstack-p5-s4-n1850.json` | `rq1-partition-openstack-p7-s5-n1850.json` |
| `rq1-partition-openstack-p8-s6-n1850.json` | `rq1-partition-openstack-p9-s7-n1850.json` |
| `rq1-placebo-openstack-s2.json` | `rq1-placebo-openstack-s3.json` |
| `rq1-placebo-openstack-seeds.json` | `rq1-placebo-openstack-v2-s2.json` |
| `rq1-placebo-openstack-v2-s3.json` | `rq1-placebo-openstack-v2-s4.json` |
| `rq1-placebo-openstack-v2-s5.json` | `rq1-placebo-openstack-v2.json` |
| `rq1-placebo-openstack-v3-p13-s2.json` | `rq1-placebo-openstack-v3-p13.json` |
| `rq1-placebo-openstack-v3-p2-s2.json` | `rq1-placebo-openstack-v3-p2.json` |
| `rq1-placebo-openstack-v3-p29-s2.json` | `rq1-placebo-openstack-v3-p29.json` |
| `rq1-placebo-openstack-v3-p8-s2.json` | `rq1-placebo-openstack-v3-p8.json` |
| `rq1-placebo-openstack-v3-s2.json` | `rq1-placebo-openstack-v3-s3.json` |
| `rq1-placebo-openstack-v3-s4.json` | `rq1-placebo-openstack-v3-s5.json` |
| `rq1-placebo-openstack-v3.json` | `rq1-placebo-openstack.json` |
| `rq1-placebo-qt-s2.json` | `rq1-placebo-qt-s3.json` |
| `rq1-placebo-qt-seeds.json` | `rq1-placebo-qt.json` |

## `scripts/power_rq1.py`

Minimum detectable matched-minus-mismatched exact-match difference, from an RQ1 pilot.

| artifact | |
|---|---|
| `power-rq1-report-grade.txt` | `power-rq1-windows.txt` |

## `scripts/project_windows.py`

How large will the sealed test window be, and does the capture model predict the dev one?

| artifact | |
|---|---|
| `project-windows-openstack-v3.json` |  |

## `scripts/prompt_format_probe.py`

How much of the pilot's base-model floor is the prompt format?

| artifact | |
|---|---|
| `prompt-format-probe.json` |  |

## `scripts/quasi_independence.py`

Does the lag distribution hold still across creation cohorts?

| artifact | |
|---|---|
| `quasi-independence.json` |  |

## `scripts/rq1_pilot.py`

Pilot-scale RQ1: does the matched adapter beat the mismatched one, per organization?

| artifact | |
|---|---|
| `calibration-marker-0.05.json` | `calibration-marker-0.1.json` |
| `calibration-marker-0.25-fp32.json` | `calibration-marker-0.25.json` |
| `calibration-marker-0.json` | `calibration-marker-1.json` |
| `calibration-sym-0-s2.json` | `calibration-sym-0-s3.json` |
| `calibration-sym-0.25.json` | `calibration-sym-0.json` |
| `calibration-sym-1.json` | `projects-qt-creator_qt-creator-qt_qtbase-n788.json` |
| `projects-qt-creator_qt-creator-qt_qtbase.json` | `projects-qt-creator_qt-creator-qt_qtdeclarative.json` |
| `projects-qt_qtbase-qt_qtdeclarative.json` | `rq1-pilot-equalized.json` |
| `rq1-pilot-fp32-pilot.json` | `rq1-pilot.json` |
| `rq1-windows-qtfull-fp32-s2.json` | `rq1-windows-qtfull-fp32-s3.json` |
| `rq1-windows-qtfull-fp32.json` | `rq1-windows-qtfull-s2.json` |
| `rq1-windows-qtfull-s3.json` | `rq1-windows-qtfull.json` |
| `rq1-windows-r256.json` | `rq1-windows-s2-r256.json` |
| `rq1-windows-s3-r256.json` | `rq1-windows.json` |

## `scripts/seed_effect.py`

How large is the seed main effect: the shift a retrained seed gives every change at once?

| artifact | |
|---|---|
| `seed-effect-placebo-openstack-v2.json` | `seed-effect-placebo-openstack-v3.json` |
| `seed-effect-placebo-openstack.json` | `seed-effect-placebo-qt.json` |
| `seed-effect-qtfull.json` | `seed-effect-rq1-qtfull.json` |
| `seed-effect-sym-0.json` |  |

## `scripts/sensitivity.py`

The smallest organizational effect the confirmatory design detects, by seed count.

| artifact | |
|---|---|
| `sensitivity-b0.0098.json` | `sensitivity-b0.json` |

## `scripts/separability.py`

Can a classifier tell the organizations apart from their review text?

| artifact | |
|---|---|
| `separability-code.json` | `separability.json` |

## `scripts/separability_over_time.py`

Is the organizational signal fading while the study measures it?

| artifact | |
|---|---|
| `separability-over-time.json` | `style-drift-code.json` |
| `style-drift.json` |  |

## `scripts/sibling_leakage.py`

How much of an evaluated half's dev window a sibling half's training data already holds.

| artifact | |
|---|---|
| `sibling-leakage.json` |  |

## `scripts/split_criteria.py`

Whether an organization's registered split qualifies for a confirmatory H1 cell.

| artifact | |
|---|---|
| `split-criteria-openstack-v2.json` | `split-criteria-openstack.json` |

## `scripts/subspace_split.py`

Would keeping the client-specific subspace local take the house style with it?

| artifact | |
|---|---|
| `subspace-split-cpp-256-c256.json` | `subspace-split-cpp-rebuilt-c128.json` |
| `subspace-split.json` |  |

## `scripts/training_size.py`

The fixed training size N every adapter trains at, derived from the organization's own splits.

| artifact | |
|---|---|
| `training-size-openstack.json` |  |

## `scripts/window_report.py`

What the collected windows contain, and how much leaks across their boundaries.

| artifact | |
|---|---|
| `window-report-openstack-v3.json` | `window-report-openstack.json` |
| `window-report-qt.json` |  |

## Written by no script this page can find

These are committed artifacts that no `claim_result`, no `--out` example and no
script name accounts for. Each is a measurement whose producer has to be read out
of the research log rather than out of the code.

- `manifest-openstack-v2.json`
- `rq1-qtfull-fp32-seeds.json`
- `rq1-r256-seeds.json`
- `stratified-coverage-0.95.json`
- `stratified-coverage-0.975.json`
- `stratified-coverage-h1-0.95.json`
- `stratified-coverage-h1-0.975.json`
