# Artifact index

Every measurement this study rests on is a committed file under
`datasets/results/`, and every one of them was written by a script in this
repository. This page is generated from those scripts: 138 artifacts under
31 writers, grouped by the script that wrote them, each described by
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

## `scripts/decoding_check.py`

Is greedy decoding what amplifies a planted convention?

| artifact | |
|---|---|
| `decoding-marker-0.25-t1.0.json` |  |

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

## `scripts/interval_calibration.py`

False-positive rate of the registered pairs cluster bootstrap, under a true null.

| artifact | |
|---|---|
| `interval-calibration.json` |  |

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

## `scripts/pilot.py`

Pilot: does an ADAPTED 7B clear the exact-match floor on this corpus?

| artifact | |
|---|---|
| `pilot-outcomes.json` |  |

## `scripts/placebo_corpus.py`

Two pseudo-organizations built from one organization's own projects.

| artifact | |
|---|---|
| `rq1-placebo-openstack-s2.json` | `rq1-placebo-openstack-s3.json` |
| `rq1-placebo-openstack-seeds.json` | `rq1-placebo-openstack.json` |
| `rq1-placebo-qt-s2.json` | `rq1-placebo-qt-s3.json` |
| `rq1-placebo-qt-seeds.json` | `rq1-placebo-qt.json` |

## `scripts/power_rq1.py`

Minimum detectable matched-minus-mismatched exact-match difference, from an RQ1 pilot.

| artifact | |
|---|---|
| `power-rq1-report-grade.txt` | `power-rq1-windows.txt` |

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

## `scripts/subspace_split.py`

Would keeping the client-specific subspace local take the house style with it?

| artifact | |
|---|---|
| `subspace-split-cpp-256-c256.json` | `subspace-split-cpp-rebuilt-c128.json` |
| `subspace-split.json` |  |

## `scripts/window_report.py`

What the collected windows contain, and how much leaks across their boundaries.

| artifact | |
|---|---|
| `window-report-openstack.json` | `window-report-qt.json` |

## Written by no script this page can find

These are committed artifacts that no `claim_result`, no `--out` example and no
script name accounts for. Each is a measurement whose producer has to be read out
of the research log rather than out of the code.

- `rq1-qtfull-fp32-seeds.json`
- `rq1-r256-seeds.json`
- `stratified-coverage-0.95.json`
- `stratified-coverage-0.975.json`
