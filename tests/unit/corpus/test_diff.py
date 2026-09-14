"""Hunks from Gerrit's own diff, against a payload captured from review.opendev.org."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sphragis.corpus.examples import METADATA_FILES, hunks_from_diff, is_code_file

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def _real_diff() -> dict:
    return json.loads((FIXTURES / "gerrit_diff.json").read_text())


def test_the_fixture_is_a_real_capture_with_the_documented_shape() -> None:
    diff = _real_diff()
    assert {"content", "meta_a", "meta_b"} <= set(diff)
    kinds = [set(b) & {"a", "b", "ab"} for b in diff["content"]]
    assert any(k == {"a", "b"} for k in kinds), "expected at least one changed block"
    assert any(k == {"ab"} for k in kinds), "expected at least one common block"


def test_hunks_come_only_from_changed_blocks() -> None:
    hunks = hunks_from_diff(_real_diff())
    assert len(hunks) == 2
    assert all(h.before or h.after for h in hunks)


def test_line_numbers_count_common_blocks_not_just_changed_ones() -> None:
    # The first changed block sits at line 1; the second follows 38 common lines,
    # so it must be line 40, not line 2. Getting this wrong silently misanchors
    # every comment in the corpus.
    hunks = hunks_from_diff(_real_diff())
    assert hunks[0].before_start == 1
    assert hunks[1].before_start == 40


def test_an_empty_diff_yields_no_hunks() -> None:
    assert hunks_from_diff({"content": [{"ab": ["unchanged"]}]}) == []


def test_a_pure_insertion_records_an_after_with_no_before() -> None:
    hunks = hunks_from_diff({"content": [{"ab": ["x"]}, {"b": ["added"]}]})
    assert len(hunks) == 1
    assert hunks[0].before == () and hunks[0].after == ("added",)


def test_commit_message_and_patchset_level_are_not_code() -> None:
    # The study measures code and the review comments on it, and excludes commit
    # metadata by design; the scope statement in the report depends on this.
    for path in METADATA_FILES:
        assert is_code_file(path) is False
    assert is_code_file("neutron_tempest_plugin/scenario/test_ovn_bgp.py") is True


@pytest.mark.parametrize("path", ["/COMMIT_MSG", "/PATCHSET_LEVEL", "/MERGE_LIST"])
def test_every_gerrit_pseudo_file_is_excluded(path: str) -> None:
    assert is_code_file(path) is False


def test_a_comment_on_the_final_patch_set_has_no_successor_to_diff() -> None:
    # Measured on live OpenStack data: 4 of 15 sampled comments raised on the diff
    # endpoint, because they sit on the last patch set and there is no n+1 to compare.
    # An example needs the author's response, so these are dropped, not errors.
    from sphragis.corpus.examples import has_successor_revision

    assert has_successor_revision(patch_set=1, revision_count=3) is True
    assert has_successor_revision(patch_set=2, revision_count=3) is True
    assert has_successor_revision(patch_set=3, revision_count=3) is False
    assert has_successor_revision(patch_set=4, revision_count=3) is False


def test_a_single_revision_change_can_never_produce_an_example() -> None:
    from sphragis.corpus.examples import has_successor_revision

    assert has_successor_revision(patch_set=1, revision_count=1) is False
