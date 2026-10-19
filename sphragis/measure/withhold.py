"""Model text withheld from published results: a keyed digest stands in place of each text.

A generation conditioned on review comments, or a summary distilled from them, can repeat a
comment word for word, and a comment can be found again on its host, so no review text is
published (`docs/protocol.md`). A plain SHA-256 of such a text is no shield: anyone can hash
every public comment and match the digests (EDPB Guidelines 01/2025 on pseudonymisation, fn. 23).
An HMAC under a secret key is not a lookup, and a holder of the key can still check that two runs
generated the same text. The full texts stay in the cluster's results root, released only under a
data-use agreement (research log, 2026-10-19).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping
from typing import Any

KEY_ENV = "SPHRAGIS_PREDICTION_KEY"
#: 256 bits: SP 800-224 (ipd) R2 asks at least 128 and no more than SHA-256's 512-bit block.
KEY_BYTES = 32
SUFFIX = "_hmac"

#: Fields holding model text conditioned on or distilled from review comments, by the file-name
#: prefix of the results that hold them (`""`: every file). Text from prompts without review text
#: stays: the contamination battery's completions of code and the rules read from written guides.
WITHHELD: Mapping[str, frozenset[str]] = {
    "": frozenset({"prediction"}),
    "determinism-": frozenset({"passes"}),
    "rules-reviews-": frozenset({"map_lists", "rules", "file", "reduce_answer"}),
}


def load_key(environ: Mapping[str, str] = os.environ) -> bytes:
    """The withholding key from the environment, or exit."""
    value = environ.get(KEY_ENV, "").strip()
    try:
        key = bytes.fromhex(value)
    except ValueError:
        key = b""
    if len(key) != KEY_BYTES:
        raise SystemExit(
            f"{KEY_ENV} must hold {KEY_BYTES} bytes as hex. Generate one with "
            '`python -c "import secrets; print(secrets.token_hex(32))"` into .env, and keep it '
            "out of git: it is what keeps the digests from being matched against public text."
        )
    return key


def key_id(key: bytes) -> str:
    """Names the key a file's digests were made under without revealing it."""
    return hmac.new(key, b"sphragis withholding key id", hashlib.sha256).hexdigest()[:16]


def digest(text: str, key: bytes) -> str:
    """HMAC-SHA256 of a text's UTF-8 bytes, as hex."""
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def fields_for(name: str) -> frozenset[str]:
    """The fields withheld in a results file of this name."""
    return frozenset().union(*(f for prefix, f in WITHHELD.items() if name.startswith(prefix)))


def _texts(value: Any) -> int:
    if isinstance(value, str):
        return 1
    if isinstance(value, Mapping):
        return sum(_texts(v) for v in value.values())
    if isinstance(value, list | tuple):
        return sum(_texts(v) for v in value)
    return 0


def _digested(value: Any, key: bytes) -> Any:
    if isinstance(value, str):
        return digest(value, key)
    if isinstance(value, Mapping):
        return {k: _digested(v, key) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_digested(v, key) for v in value]
    return value


def text_left(document: Any, name: str) -> list[str]:
    """Where a document still carries text its file's fields withhold, as JSON paths."""
    fields = fields_for(name)
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():
                if k in fields and _texts(v):
                    found.append(f"{path}/{k}")
                else:
                    walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}")

    walk(document, "")
    return found


def withhold(document: Any, name: str, key: bytes) -> tuple[Any, int]:
    """The document with each withheld field's texts replaced by digests, and how many were.

    A field `f` holding text becomes `f_hmac` in the same place, its shape kept and each string
    in it digested; a field holding none (a prediction interval, a null) is left alone. A copy
    already withheld that still carries text is refused rather than half-merged.
    """
    fields = fields_for(name)
    count = 0

    def walk(node: Any) -> Any:
        nonlocal count
        if isinstance(node, Mapping):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k in fields and _texts(v):
                    count += _texts(v)
                    out[f"{k}{SUFFIX}"] = _digested(v, key)
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    result = walk(document)
    if not count:
        return document, 0
    if not isinstance(result, dict):
        raise ValueError(f"{name}: withheld text outside a JSON object")
    if "withheld_text" in result:
        raise ValueError(f"{name} is already a withheld copy yet still carries text")
    result["withheld_text"] = {
        "fields": sorted({p.rsplit("/", 1)[-1] for p in text_left(document, name)}),
        "texts": count,
        "algorithm": "HMAC-SHA256",
        "key_id": key_id(key),
        "full_text": "the cluster's results root, under a data-use agreement (docs/protocol.md)",
    }
    return result, count


def require_text(document: Mapping[str, Any], label: str) -> None:
    """Exit when a reader that needs model text is handed a published, withheld copy."""
    if "withheld_text" in document:
        raise SystemExit(
            f"{label} is a published copy: its {', '.join(document['withheld_text']['fields'])} "
            "are digests. Run this on the full-text copy in the cluster's results root."
        )
