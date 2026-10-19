"""Each research-log table `scripts/log_tables.py` prints is in the log as printed."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("log_tables", ROOT / "scripts" / "log_tables.py")
assert _spec is not None and _spec.loader is not None
log_tables = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(log_tables)

RESULTS = ROOT / "datasets" / "results"
LOG = (ROOT / "docs" / "research-log.md").read_text()

#: The tables the log quotes from these artifacts, each as the generator prints it.
TABLES = {
    "Qt at K = 10": lambda: log_tables.sensitivity(
        RESULTS / "partition-sensitivity-qt-rp1.0-logprob_per_token.json"
    ),
    "Qt at K = 15": lambda: log_tables.sensitivity(
        RESULTS / "partition-sensitivity-qt-rp1.0-logprob_per_token-k15.json"
    ),
    "Wikimedia at K = 20": lambda: log_tables.sensitivity(
        RESULTS / "partition-sensitivity-wikimedia-rp1.0-logprob_per_token-k20.json"
    ),
    "Qt's K": lambda: log_tables.k_coverage(RESULTS / "k-coverage-qt-likelihood.json"),
    "Wikimedia's K": lambda: log_tables.k_coverage(
        RESULTS / "k-coverage-wikimedia-likelihood.json"
    ),
    "s split, OpenStack and Wikimedia": lambda: log_tables.split(["openstack", "wikimedia"]),
    "s split, Qt": lambda: log_tables.split(["qt"]),
    "s split without lexer-unsure rows": lambda: log_tables.split(
        ["openstack", "wikimedia", "qt"], lexer_sure=True
    ),
}


@pytest.mark.parametrize("name", sorted(TABLES))
def test_the_log_quotes_each_table_as_its_artifact_prints_it(name: str) -> None:
    table = TABLES[name]()
    assert table in LOG, f"{name}: the log's table differs from\n{table}"
