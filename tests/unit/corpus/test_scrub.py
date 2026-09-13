"""Salted identity stripping at ingestion."""

from __future__ import annotations

from sphragis.corpus.scrub import pseudonym, scrub

SALT = "test-salt"


def test_pseudonym_is_stable_salted_and_short() -> None:
    assert pseudonym("alice@example.org", SALT) == pseudonym("alice@example.org", SALT)
    assert pseudonym("alice@example.org", SALT) != pseudonym("alice@example.org", "other")
    assert pseudonym("alice@example.org", SALT) != pseudonym("bob@example.org", SALT)
    token = pseudonym("alice@example.org", SALT)
    assert len(token) == 12 and all(c in "0123456789abcdef" for c in token)


def test_scrub_replaces_account_info_with_a_single_pseudonym() -> None:
    change = {
        "owner": {
            "_account_id": 1000096,
            "name": "Alice Example",
            "email": "alice@example.org",
            "username": "alice",
        }
    }
    out = scrub(change, SALT)
    assert out["owner"] == {"_account_id": pseudonym(1000096, SALT)}
    assert "alice" not in repr(out)


def test_scrub_redacts_emails_in_free_text() -> None:
    out = scrub({"message": "ping bob@example.org about this"}, SALT)
    assert "bob@example.org" not in out["message"]
    assert pseudonym("bob@example.org", SALT) in out["message"]


def test_scrub_preserves_structural_fields_and_does_not_mutate_input() -> None:
    change = {
        "change_id": "I1234",
        "project": "openstack/nova",
        "created": "2024-10-02 11:00:00.000000000",
        "revisions": {"abc": {"_number": 1}},
        "owner": {"_account_id": 7, "name": "Carol"},
    }
    out = scrub(change, SALT)
    assert out["change_id"] == "I1234"
    assert out["project"] == "openstack/nova"
    assert out["created"] == "2024-10-02 11:00:00.000000000"
    assert out["revisions"]["abc"]["_number"] == 1
    assert change["owner"]["name"] == "Carol"
