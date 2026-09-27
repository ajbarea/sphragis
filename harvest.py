#!/usr/bin/env python3
"""Assert every number on the docs site against the artifact that produced it.

The site restates decisions that were made from measurements, and a restatement rots the
way a generated table rots: the apparatus moves, the number in the page does not, and
nothing says so. So each figure quoted under `docs/` is registered here as a claim
against a path into a committed artifact, and a figure nothing resolves is reported as
UNBACKED rather than passed over.

A claim passes when the artifact's value rounds to the literal in the page, at the
literal's own precision. Quoting 0.031 for 0.031578947368421074 is correct reporting, and
a comparator demanding string equality would reject every rounded figure on the site.

`--index` regenerates `docs/artifacts.md` from what the scripts declare they write, so the
index is derived rather than maintained. Without it the index is checked, not rewritten,
which is what the test suite runs.

Run: uv run --no-sync --no-active python harvest.py           # report
     uv run --no-sync --no-active python harvest.py --check   # the same, as a gate
     uv run --no-sync --no-active python harvest.py --index   # rewrite docs/artifacts.md
     uv run --no-sync --no-active python harvest.py --scan    # figures no claim asserts

The claim machinery is a copy of `papers/org-house-style/harvest.py`, not an import. That
paper reads this repository's artifacts from outside it; a repository whose stated
invariant is to reproduce years from now does not take a dependency on a manuscript.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent
DOCS = ROOT / "docs"
SCRIPTS = ROOT / "scripts"
RESULTS = "datasets/results"
INDEX_PAGE = DOCS / "artifacts.md"

# Artifacts the pages quote. The corpus counts live in the frozen manifests rather than
# under datasets/results, because the window sizes are a property of the seal and the
# window report that also carries them is a pre-refreeze artifact.
MANIFEST_OS = "datasets/gerrit/openstack/manifest.json"
MANIFEST_QT = "datasets/gerrit/qt/manifest.json"

DEV = f"{RESULTS}/rq1-qtfull-fp32-seeds.json"
PILOT = f"{RESULTS}/pilot-outcomes.json"
FP32_PILOT = f"{RESULTS}/rq1-pilot-fp32-pilot.json"
UNEQUAL = f"{RESULTS}/rq1-pilot.json"
WINDOW = f"{RESULTS}/window-report-openstack.json"
CONTAMINATION = f"{RESULTS}/contamination-openstack-6mo-with_context-gapk.json"
SENSITIVITY_POINT = f"{RESULTS}/sensitivity-b0.json"
SENSITIVITY_UPPER = f"{RESULTS}/sensitivity-b0.0098.json"
CALIBRATION = f"{RESULTS}/interval-calibration.json"
INFORMATIVENESS = f"{RESULTS}/cluster-informativeness.json"
COVERAGE = f"{RESULTS}/crossed-coverage.json"
SEED_EFFECT = f"{RESULTS}/seed-effect-qtfull.json"
SEED_EFFECT_1800 = f"{RESULTS}/seed-effect-sym-0.json"
CENSORING = f"{RESULTS}/censoring.json"
MARKER_1 = f"{RESULTS}/calibration-marker-1.json"

DEV_CROSSED = ["readings", "pooled", "crossed", "per_org"]
DEV_AVERAGED = ["readings", "change_averaged", "crossed", "per_org"]
NON_DEGENERACY = ["outcome_neutral", "checks", "#name=non_degeneracy", "evidence", "exact_match"]
QT_CONTROL = ["outcome_neutral", "checks", "#name=positive_control:qt|s1", "evidence", "interval"]
NEAR_DUPLICATE = ["openstack", "near_duplicate_rate", "train->dev"]

#: claim id -> (page under docs/, the literal as it appears, artifact path, path into the
#: artifact, options). An option dict may carry `scale` (the page quotes a percentage of a
#: rate), `exact` (the literal must equal the value, not round to it), `text` (the value is
#: a word rather than a number, and with `code` is counted only where the page sets it as
#: inline code, because "pass" and "mixed" are also ordinary words on a page about a gate),
#: or `occurrences` (the literal is quoted more than once).
CLAIMS: list[tuple[Any, ...]] = [
    # ---- protocol.md: the corpus, and where the dev-window reading stands ----
    ("corpus_os", "protocol.md", "5,487", MANIFEST_OS, ["counts"], {"reduce": "sum"}),
    ("corpus_qt", "protocol.md", "9,606", MANIFEST_QT, ["counts"], {"reduce": "sum"}),
    ("seal_os", "protocol.md", "600", MANIFEST_OS, ["counts", "pilot"]),
    ("train_os", "protocol.md", "4,322", MANIFEST_OS, ["counts", "train"]),
    ("dev_os_n", "protocol.md", "565", MANIFEST_OS, ["counts", "dev"]),
    ("seal_qt", "protocol.md", "1,146", MANIFEST_QT, ["counts", "pilot"]),
    ("train_qt", "protocol.md", "7,563", MANIFEST_QT, ["counts", "train"]),
    ("dev_qt_n", "protocol.md", "897", MANIFEST_QT, ["counts", "dev"]),
    # The seal itself: the test window's content hash over no content. Quoted rather than
    # its count, because a page saying the window holds "0" examples is a page whose claim
    # any stray zero satisfies.
    (
        "seal_hash",
        "protocol.md",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        MANIFEST_OS,
        ["hashes", "test"],
        {"text": True, "occurrences": 2},
    ),
    ("dev_os", "protocol.md", "+0.0230", DEV, [*DEV_CROSSED, "openstack", "estimate"]),
    (
        "dev_os_low",
        "protocol.md",
        "+0.0000",
        DEV,
        [*DEV_CROSSED, "openstack", "low"],
        {"exact": True},
    ),
    ("dev_os_high", "protocol.md", "+0.0457", DEV, [*DEV_CROSSED, "openstack", "high"]),
    ("dev_qt", "protocol.md", "+0.0316", DEV, [*DEV_CROSSED, "qt", "estimate"]),
    ("dev_qt_low", "protocol.md", "+0.0089", DEV, [*DEV_CROSSED, "qt", "low"]),
    ("dev_qt_high", "protocol.md", "+0.0567", DEV, [*DEV_CROSSED, "qt", "high"]),
    (
        "dev_verdict",
        "protocol.md",
        "mixed",
        DEV,
        [*DEV_CROSSED[:-1], "verdict"],
        {"text": True, "code": True},
    ),
    (
        "sensitivity_low",
        "protocol.md",
        "0.0129",
        SENSITIVITY_POINT,
        ["organizations", "qt", "by_seeds", "3", "minimum_detectable_effect"],
    ),
    (
        "sensitivity_high",
        "protocol.md",
        "0.0235",
        SENSITIVITY_UPPER,
        ["organizations", "openstack", "by_seeds", "3", "minimum_detectable_effect"],
    ),
    # ---- registered-decisions.md ----
    (
        "rd_cluster_r_os",
        "registered-decisions.md",
        "-0.026",
        INFORMATIVENESS,
        ["summary", "openstack", "correlation_median"],
    ),
    (
        "rd_cluster_r_qt",
        "registered-decisions.md",
        "-0.006",
        INFORMATIVENESS,
        ["summary", "qt", "correlation_median"],
    ),
    (
        "rd_averaged_verdict",
        "registered-decisions.md",
        "pass",
        DEV,
        [*DEV_AVERAGED[:-1], "verdict"],
        {"text": True, "code": True},
    ),
    (
        "rd_averaged_os",
        "registered-decisions.md",
        "+0.0380",
        DEV,
        [*DEV_AVERAGED, "openstack", "estimate"],
    ),
    (
        "rd_averaged_qt",
        "registered-decisions.md",
        "+0.0334",
        DEV,
        [*DEV_AVERAGED, "qt", "estimate"],
    ),
    (
        "rd_boundary_qt",
        "registered-decisions.md",
        "+0.045",
        UNEQUAL,
        ["verdict", "binding", "qt", "estimate"],
    ),
    (
        "rd_boundary_qt_low",
        "registered-decisions.md",
        "+0.000",
        UNEQUAL,
        ["verdict", "binding", "qt", "low"],
        {"exact": True},
    ),
    (
        "rd_boundary_dev_low",
        "registered-decisions.md",
        "+0.0000",
        DEV,
        [*DEV_CROSSED, "openstack", "low"],
        {"exact": True},
    ),
    (
        "rd_test_censoring",
        "registered-decisions.md",
        "0.75",
        CENSORING,
        ["differential_missing", "test_by_fetch_month", "2027-02"],
        {"scale": 100},
    ),
    (
        "rd_dev_censoring",
        "registered-decisions.md",
        "10.33",
        CENSORING,
        ["differential_missing", "windows", "dev"],
        {"scale": 100},
    ),
    (
        "rd_minkpp",
        "registered-decisions.md",
        "-0.075",
        CONTAMINATION,
        ["report", "min_k_plus_plus", "gap"],
    ),
    (
        "rd_guided",
        "registered-decisions.md",
        "+0.003",
        CONTAMINATION,
        ["report", "guided_completion", "gap"],
    ),
    (
        "rd_leak_07",
        "registered-decisions.md",
        "1.06",
        WINDOW,
        [*NEAR_DUPLICATE, "0.7", "rate"],
        {"scale": 100},
    ),
    (
        "rd_leak_08",
        "registered-decisions.md",
        "0.00",
        WINDOW,
        [*NEAR_DUPLICATE, "0.8", "rate"],
        {"scale": 100},
    ),
    ("rd_seed_effect", "registered-decisions.md", "0.000", SEED_EFFECT, ["sigma_b"]),
    ("rd_seed_bound", "registered-decisions.md", "0.0098", SEED_EFFECT, ["sigma_b_upper_95"]),
    ("rd_seed_effect_1800", "registered-decisions.md", "0.013", SEED_EFFECT_1800, ["sigma_b"]),
    (
        "rd_coverage_median",
        "registered-decisions.md",
        "0.122",
        COVERAGE,
        ["cells", "#seeds=3,sigma_b=0.02"],
        {"reduce": "add:median_seed_above+median_seed_below"},
    ),
    (
        "rd_coverage_crossed",
        "registered-decisions.md",
        "0.073",
        COVERAGE,
        ["cells", "#seeds=3,sigma_b=0.02"],
        {"reduce": "add:crossed_above+crossed_below"},
    ),
    (
        "rd_sensitivity_os",
        "registered-decisions.md",
        "+0.0160",
        SENSITIVITY_POINT,
        ["organizations", "openstack", "by_seeds", "3", "minimum_detectable_effect"],
    ),
    (
        "rd_sensitivity_os_upper",
        "registered-decisions.md",
        "+0.0235",
        SENSITIVITY_UPPER,
        ["organizations", "openstack", "by_seeds", "3", "minimum_detectable_effect"],
    ),
    # ---- outcome-neutral.md ----
    (
        "on_minkpct",
        "outcome-neutral.md",
        "-0.130",
        CONTAMINATION,
        ["report", "min_k_percent", "gap"],
    ),
    (
        "on_minkpp",
        "outcome-neutral.md",
        "-0.075",
        CONTAMINATION,
        ["report", "min_k_plus_plus", "gap"],
    ),
    ("on_gapk", "outcome-neutral.md", "-0.056", CONTAMINATION, ["report", "gap_k_percent", "gap"]),
    (
        "on_guided",
        "outcome-neutral.md",
        "+0.003",
        CONTAMINATION,
        ["report", "guided_completion", "gap"],
    ),
    ("on_control", "outcome-neutral.md", "+0.333", PILOT, ["interval", "estimate"]),
    ("on_control_low", "outcome-neutral.md", "+0.133", PILOT, ["interval", "low"]),
    ("on_control_high", "outcome-neutral.md", "+0.565", PILOT, ["interval", "high"]),
    ("on_base_em", "outcome-neutral.md", "0.074", FP32_PILOT, [*NON_DEGENERACY, "base|openstack"]),
    (
        "on_adapted_em",
        "outcome-neutral.md",
        "0.407",
        FP32_PILOT,
        [*NON_DEGENERACY, "adapter:openstack|openstack|s1"],
    ),
    ("on_qt_control", "outcome-neutral.md", "+0.218", FP32_PILOT, [*QT_CONTROL, "estimate"]),
    ("on_qt_control_low", "outcome-neutral.md", "+0.120", FP32_PILOT, [*QT_CONTROL, "low"]),
    ("on_qt_control_high", "outcome-neutral.md", "+0.337", FP32_PILOT, [*QT_CONTROL, "high"]),
    (
        "on_leak_07",
        "outcome-neutral.md",
        "1.06",
        WINDOW,
        [*NEAR_DUPLICATE, "0.7", "rate"],
        {"scale": 100},
    ),
    (
        "on_leak_06",
        "outcome-neutral.md",
        "1.42",
        WINDOW,
        [*NEAR_DUPLICATE, "0.6", "rate"],
        {"scale": 100},
    ),
    (
        "on_leak_05",
        "outcome-neutral.md",
        "1.77",
        WINDOW,
        [*NEAR_DUPLICATE, "0.5", "rate"],
        {"scale": 100},
    ),
    ("on_corpus", "outcome-neutral.md", "5,487", MANIFEST_OS, ["counts"], {"reduce": "sum"}),
    (
        "on_marker_a",
        "outcome-neutral.md",
        "+0.310",
        MARKER_1,
        ["verdict", "binding", "a", "estimate"],
    ),
    (
        "on_marker_b",
        "outcome-neutral.md",
        "+0.316",
        MARKER_1,
        ["verdict", "binding", "b", "estimate"],
    ),
    (
        "on_marker_verdict",
        "outcome-neutral.md",
        "pass",
        MARKER_1,
        ["verdict", "verdict"],
        {"text": True, "code": True},
    ),
    (
        "on_fpr_19",
        "outcome-neutral.md",
        "6.0",
        CALIBRATION,
        ["ladder", "19", "false_positive_rate"],
        {"scale": 100},
    ),
    (
        "on_fpr_91",
        "outcome-neutral.md",
        "4.8",
        CALIBRATION,
        ["ladder", "91", "false_positive_rate"],
        {"scale": 100},
    ),
]


# ---------------------------------------------------------------------------
# Claim resolution
# ---------------------------------------------------------------------------


def step(data: Any, part: str) -> Any:
    """One path element: a key, a list index, or `#field=value[,field=value]` selecting a row.

    Several conditions because a coverage cell is identified by its seed count and its seed
    effect together, and either alone matches four rows.
    """
    if part.startswith("#"):
        wanted = dict(condition.split("=", 1) for condition in part[1:].split(","))
        matches = [row for row in data if all(str(row.get(k)) == v for k, v in wanted.items())]
        if len(matches) != 1:
            raise KeyError(f"{len(matches)} rows match {wanted}; a claim needs exactly one")
        return matches[0]
    if isinstance(data, list):
        return data[int(part)]
    if part not in data:
        raise KeyError(part)
    return data[part]


def resolve(artifact: str, path: list[str], options: dict[str, Any]) -> tuple[Any, str | None]:
    """The artifact's value at `path`, or None and the reason it did not resolve."""
    path = [str(part) for part in path]
    file = ROOT / artifact
    if not file.exists():
        return None, f"artifact missing: {artifact}"
    data = json.loads(file.read_text())
    for part in path:
        try:
            data = step(data, part)
        except (KeyError, IndexError, TypeError) as error:
            return None, f"{'.'.join(path)} does not resolve in {artifact} ({error})"

    if options.get("text"):
        if not isinstance(data, str):
            return None, f"{'.'.join(path)} in {artifact} is {type(data).__name__}, not text"
        return data, None

    reduce = options.get("reduce")
    if reduce == "sum":
        # A window count table quoted as its total.
        data = sum(data.values())
    elif reduce and reduce.startswith("add:"):
        # A two-sided rate is stored as its two tails, and the page quotes the sum.
        fields = reduce.split(":", 1)[1].split("+")
        try:
            data = sum(data[field] for field in fields)
        except (KeyError, TypeError) as error:
            return None, f"{'.'.join(path)} in {artifact} cannot add {fields} ({error})"
    # bool is an int in Python, and a serialization regression writing true where a bound
    # belongs would otherwise verify as 0.0 against a "+0.0000" literal.
    if isinstance(data, bool) or not isinstance(data, (int, float)):
        return None, f"{'.'.join(path)} in {artifact} is {type(data).__name__}, not a number"
    return data * options.get("scale", 1), None


def occurrences(text: str, literal: str) -> int:
    """How many times the literal appears in the page as a whole number, not a substring.

    A plain `in` test passes when the literal is a fragment of another number: "0.0" occurs
    inside 0.075 and 0.003, so the leakage claim it guards could not fail. The count is
    returned rather than a boolean because a figure quoted twice can drift in one copy only,
    which leaves the page inconsistent with itself while every claim still resolves.
    """
    # An unsigned literal must not match a signed number either: 0.000 occurs on its own as
    # the measured seed effect and again as +0.000, a bound of a different interval.
    before = r"(?<![0-9.,])" if literal[0] in "+-" else r"(?<![0-9.,+-])"
    return len(re.findall(before + re.escape(literal) + r"(?![0-9])", text))


def rounds_to(value: float, literal: str) -> bool:
    """The artifact's value, rounded to the literal's precision, is the literal."""
    text = re.sub(r"[+,%]", "", literal)
    decimals = len(text.partition(".")[2])
    return f"{value:.{decimals}f}" == f"{float(text):.{decimals}f}"


def tree_is_dirty() -> bool:
    """Whether the artifacts have uncommitted work, which makes a check unverifiable."""
    try:
        dirty = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--", "datasets"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(dirty.strip())


def check_claims() -> tuple[list[str], list[str], list[str]]:
    """Every claim, split into verified, drifted and unbacked."""
    verified, drifted, unbacked = [], [], []
    for claim in CLAIMS:
        cid, page, literal, artifact, path = claim[:5]
        options = claim[5] if len(claim) > 5 else {}
        file = DOCS / page
        if not file.exists():
            unbacked.append(f"{cid}: docs/{page} does not exist")
            continue
        found = occurrences(file.read_text(), f"`{literal}`" if options.get("code") else literal)
        expected = options.get("occurrences", 1)
        if found != expected:
            drifted.append(
                f"{cid}: docs/{page} holds {literal} {found} time(s), not {expected}; "
                "a copy moved, or the claim's count is stale"
            )
            continue
        value, error = resolve(artifact, path, options)
        if error:
            unbacked.append(f"{cid}: {error}")
        elif options.get("text"):
            if value != literal:
                drifted.append(f"{cid}: docs/{page} says {literal}, {artifact} says {value}")
            else:
                verified.append(cid)
        elif options.get("exact") and value != float(re.sub(r"[+,%]", "", literal)):
            drifted.append(
                f"{cid}: docs/{page} says {literal} and the claim is exact, "
                f"{artifact} says {value!r}"
            )
        elif not rounds_to(value, literal):
            drifted.append(f"{cid}: docs/{page} says {literal}, {artifact} says {value:.6g}")
        else:
            verified.append(cid)
    return verified, drifted, unbacked


#: Pages this harvest does not assert. The research log is the dated record and quotes
#: superseded and withdrawn readings on purpose, so a figure there that no current artifact
#: reproduces is the log working rather than drift. The artifact index carries no figures.
UNASSERTED = {"research-log.md", INDEX_PAGE.name}


def unclaimed() -> list[str]:
    """Figures on the authored pages that no claim asserts.

    The docstring above promises the site's numbers are checked, and a claim table can only
    promise that for the rows it holds. This makes the gap visible instead.
    """
    claimed = {(claim[1], claim[2]) for claim in CLAIMS}
    found = []
    for page in sorted(p.name for p in DOCS.glob("*.md") if p.name not in UNASSERTED):
        text = (DOCS / page).read_text()
        for match in re.finditer(r"[+-]?\d+\.\d{3,4}\b", text):
            literal = match.group()
            if (page, literal) in claimed:
                continue
            if any(literal.lstrip("+-") == other.lstrip("+-") for _, other in claimed):
                continue
            found.append(f"{page}: {literal}")
    return found


# ---------------------------------------------------------------------------
# The artifact index, derived from what the scripts declare they write
# ---------------------------------------------------------------------------


def _stem_to_glob(stem: str) -> str:
    return f"{stem}*"


def _shell_globs(raw: str, job: str) -> list[str]:
    """A `claim_result` path as globs, one per value of the variables the job enumerates.

    A variable the job documents as `VAR=a|b` has two known values and both are expanded,
    which keeps `rq1-$MODE$RESULT_SUFFIX.json` from also matching a post-processing output
    named `rq1-` something. A variable with only a default, or none, stays a wildcard: the
    default is one value of many, and narrowing to it would orphan every other run.
    """
    name = raw.rsplit("/", 1)[-1]
    enumerated = {
        var: values.split("|")
        for var, values in re.findall(r"^#\s*(\w+)=([\w.\-]+(?:\|[\w.\-]+)+)", job, re.M)
    }
    globs = [name]
    for var, values in enumerated.items():
        globs = [g.replace(f"${var}", value) for g in globs for value in values]
    return sorted({re.sub(r"\*+", "*", re.sub(r"\$\{[^}]*\}|\$\w+", "*", g)) for g in globs})


#: Where a declaration came from. A pairing read out of the research log is prose and can
#: name a script that only *reads* an artifact, so it never outranks one read out of code.
FROM_CODE = 1
FROM_LOG = 0


def declared_outputs() -> list[tuple[str, str, int, int]]:
    """(glob, writing script, tier, specificity) for every result a script declares it writes.

    Five sources, in the order a reader would check them:

    1. `claim_result` in the job scripts, which is where every cluster result is named.
    2. the `--out` path in a CPU script's own usage example.
    3. the `--out` argparse default, which on a job script's own run is a bare filename.
    4. a module-level `RESULTS = Path("datasets/results/...")`.
    5. the script's own name, kebab-cased, which is this repository's naming convention
       and is what attributes the tagged variants of a run.

    Then the research log, for the measurements whose producer the code does not name. It
    is the dated record and it states those pairings on one line; reading them from there
    beats hand-listing them here, and the tier keeps prose below code.

    Specificity is the glob's literal length, so `separability-over-time*` wins over
    `separability*` for a file both match.
    """
    declared: list[tuple[str, str, int]] = []

    for job in sorted(SCRIPTS.glob("*.sbatch")):
        text = job.read_text()
        writers = re.findall(r"python (scripts/\w+\.py)", text)
        writer = writers[0] if writers else f"scripts/{job.name}"
        for match in re.finditer(r'^\s*claim_result [A-Z_]+ "([^"]+)"', text, re.M):
            for glob in _shell_globs(match.group(1), text):
                declared.append((glob, writer, FROM_CODE))

    for script in sorted(SCRIPTS.glob("*.py")):
        text = script.read_text()
        name = f"scripts/{script.name}"
        for match in re.finditer(rf"--out[ =]+{RESULTS}/([\w.\-]+)\.\w+", text):
            declared.append((_stem_to_glob(match.group(1)), name, FROM_CODE))
        for match in re.finditer(r'"--out".*default=Path\("([\w.\-]+)\.\w+"\)', text):
            declared.append((_stem_to_glob(match.group(1)), name, FROM_CODE))
        for match in re.finditer(rf'^RESULTS = Path\("{RESULTS}/([\w.\-]+)\.\w+"\)', text, re.M):
            declared.append((_stem_to_glob(match.group(1)), name, FROM_CODE))
        declared.append((_stem_to_glob(script.stem.replace("_", "-")), name, FROM_CODE))

    declared += _log_pairs({f"scripts/{path.name}" for path in SCRIPTS.glob("*.py")})

    return [(glob, writer, tier, len(glob.replace("*", ""))) for glob, writer, tier in declared]


def _log_pairs(scripts: set[str]) -> list[tuple[str, str, int]]:
    """Artifact/script pairings stated in one dated entry of the research log.

    A handful of measurements are post-processing steps whose output name the code never
    spells: `crossed_reread` writes one file per inference precision and the precision sits
    in the middle of the name, where no prefix taken from the script can reach it. The log
    is the dated record and names both in the same entry, so the pairing is read rather
    than hand-listed.

    Scoped to the entry's opening paragraph, which is where an entry states what it
    measured and with what. Later paragraphs discuss inputs, and a pairing taken from one
    would name a reader as the writer. An entry naming two scripts cannot say which of them
    wrote the file and is skipped.
    """
    log = DOCS / "research-log.md"
    if not log.exists():
        return []
    pairs: list[tuple[str, str, int]] = []
    stems = {name.removeprefix("scripts/").removesuffix(".py"): name for name in scripts}
    for entry in re.split(r"^### ", log.read_text(), flags=re.M)[1:]:
        opening = entry.split("\n\n", 2)[:2]
        head = "\n\n".join(opening)
        named = {
            stems[stem]
            for stem in re.findall(r"`(?:scripts/)?(\w+)(?:\.py)?`", head)
            if stem in stems
        }
        if len(named) != 1:
            continue
        writer = named.pop()
        for artifact in re.findall(rf"{RESULTS}/([\w.\-]+)\.\w+", head):
            pairs.append((_stem_to_glob(artifact), writer, FROM_LOG))
    return pairs


def _summary(script: str) -> str:
    """The first line of the writing script's module docstring."""
    path = ROOT / script
    if not path.exists():
        return "no docstring: the script is gone"
    if path.suffix == ".py":
        doc = ast.get_docstring(ast.parse(path.read_text())) or ""
        return doc.strip().splitlines()[0] if doc.strip() else "no module docstring"
    # A job script's first comment paragraph after the #SBATCH block.
    for line in path.read_text().splitlines():
        if line.startswith("# ") and "SBATCH" not in line:
            return line[2:].strip()
    return "no leading comment"


def tracked_results() -> list[str]:
    """Committed files under datasets/results, newest naming first."""
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", RESULTS],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return sorted(path.rsplit("/", 1)[-1] for path in out if "/" not in path[len(RESULTS) + 1 :])


def attribute() -> tuple[dict[str, list[str]], list[str]]:
    """Every committed artifact under its writing script, and the ones nothing claims."""
    declared = declared_outputs()
    by_writer: dict[str, list[str]] = {}
    orphans: list[str] = []
    for name in tracked_results():
        # A declaration naming this artifact's whole stem beats every prefix, whatever its
        # source: a job script's `rq1-$MODE$RESULT_SUFFIX.json` reduces to `rq1-*`, which
        # also matches post-processing output it did not write, and neither the variable's
        # value nor the run tag's can be recovered from a name that concatenates them.
        # Below that, code outranks prose, and a longer prefix outranks a shorter one.
        stem = name.rsplit(".", 1)[0]
        hits = [
            (glob == _stem_to_glob(stem), tier, spec, writer)
            for glob, writer, tier, spec in declared
            if fnmatch.fnmatch(name, glob)
        ]
        if not hits:
            orphans.append(name)
            continue
        by_writer.setdefault(max(hits)[3], []).append(name)
    return by_writer, orphans


def render_index() -> str:
    """docs/artifacts.md, as the scripts and the committed artifacts describe it."""
    by_writer, orphans = attribute()
    total = sum(len(names) for names in by_writer.values())
    lines = [
        "# Artifact index",
        "",
        "Every measurement this study rests on is a committed file under",
        "`datasets/results/`, and every one of them was written by a script in this",
        f"repository. This page is generated from those scripts: {total} artifacts under",
        f"{len(by_writer)} writers, grouped by the script that wrote them, each described by",
        "that script's own summary line. Run `make docs-index` to rebuild it, and the test",
        "suite fails if it is stale.",
        "",
        "A run that varies a knob names its output after the knob, so the variants of one",
        "measurement sit together: a cluster suffix keeps an off-TIGRIS run from overwriting",
        "a GH200 result, and `RUN_TAG`, `SEEDS`, `TRAIN_SIZE` and `CLIENT_SIZE` name a run",
        "that is a different study rather than a repeat of the same one.",
        "",
    ]
    for writer in sorted(by_writer):
        names = sorted(by_writer[writer])
        lines += [
            f"## `{writer}`",
            "",
            _summary(writer),
            "",
            "| artifact | |",
            "|---|---|",
        ]
        # Two per row: the list is long and every entry is short.
        for start in range(0, len(names), 2):
            pair = names[start : start + 2]
            right = f"`{pair[1]}`" if len(pair) > 1 else ""
            lines.append(f"| `{pair[0]}` | {right} |")
        lines.append("")
    if orphans:
        lines += [
            "## Written by no script this page can find",
            "",
            "These are committed artifacts that no `claim_result`, no `--out` example and no",
            "script name accounts for. Each is a measurement whose producer has to be read out",
            "of the research log rather than out of the code.",
            "",
        ]
        lines += [f"- `{name}`" for name in orphans]
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit non-zero on any defect")
    parser.add_argument("--index", action="store_true", help="rewrite docs/artifacts.md")
    parser.add_argument("--scan", action="store_true", help="list figures no claim asserts")
    args = parser.parse_args()

    if args.scan:
        for line in unclaimed():
            print(f"  UNCLAIMED {line}")
        return

    if args.index:
        INDEX_PAGE.write_text(render_index())
        print(f"wrote {INDEX_PAGE.relative_to(ROOT)}")
        return

    if args.check and tree_is_dirty():
        print("datasets/ has uncommitted work; these numbers describe no commit")
        sys.exit(2)

    verified, drifted, unbacked = check_claims()
    stale_index = INDEX_PAGE.exists() and INDEX_PAGE.read_text() != render_index()
    for line in drifted:
        print(f"  DRIFT    {line}")
    for line in unbacked:
        print(f"  UNBACKED {line}")
    if stale_index:
        print(f"  STALE    {INDEX_PAGE.relative_to(ROOT)}; run `make docs-index`")
    print(f"\n{len(verified)} verified, {len(drifted)} drifted, {len(unbacked)} unbacked")
    # Unbacked fails the gate but not the report: a number nothing resolves is the state
    # this harvest exists to make visible, and under --check it is a defect like any other.
    sys.exit(1 if drifted or stale_index or (args.check and unbacked) else 0)


if __name__ == "__main__":
    main()
