"""Salted identity stripping, applied before any raw record reaches disk."""

from __future__ import annotations

import hashlib
import re
from typing import Any

# Repeated @domain suffixes are one address: "a@b.com@google.com" once left "@google.com" behind
# the pseudonym (found by the NoteDb parity check, 2026-09-23).
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+(?:@[A-Za-z0-9.-]+\.[A-Za-z]{2,})+")
_IDENTITY_KEYS = frozenset(
    {"name", "email", "username", "display_name", "secondary_emails", "avatars"}
)


# Account tags that describe the account's kind rather than the person, kept through the scrub.
# SERVICE_USER is how Gerrit marks a bot, and dropping it here would leave a later stage no way
# to tell a lint bot from a reviewer except by what it wrote.
_KIND_TAGS = frozenset({"SERVICE_USER"})


def pseudonym(value: object, salt: str) -> str:
    """Stable 12-hex pseudonym for one identity value."""
    digest = hashlib.sha256(f"{salt}:{value}".encode())
    return digest.hexdigest()[:12]


def _scrub_text(text: str, salt: str) -> str:
    return _EMAIL.sub(lambda m: pseudonym(m.group(0), salt), text)


def scrub(obj: Any, salt: str) -> Any:
    """Recursively replace Gerrit account identities with salted pseudonyms."""
    if isinstance(obj, dict):
        if "_account_id" in obj:
            account: dict[str, Any] = {"_account_id": pseudonym(obj["_account_id"], salt)}
            tags = [t for t in obj.get("tags") or [] if t in _KIND_TAGS]
            if tags:
                account["tags"] = tags
            return account
        return {k: (None if k in _IDENTITY_KEYS else scrub(v, salt)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(item, salt) for item in obj]
    if isinstance(obj, str):
        return _scrub_text(obj, salt)
    return obj
