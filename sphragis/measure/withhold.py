"""Model text withheld from published results: a keyed digest stands in place of each text.

A generation conditioned on review comments, or a summary distilled from them, can repeat a
comment word for word, and a comment can be found again on its host, so no review text is
published (`docs/protocol.md`). A plain SHA-256 of such a text is no shield: anyone can hash
every public comment and match the digests (EDPB Guidelines 01/2025 on pseudonymisation, fn. 23).
An HMAC under a secret key is not a lookup. Anyone can compare digests, so equal texts show as
equal across runs, fields and files; the key is needed only to test a guessed text against one. The
full texts stay in the cluster's results root, released only under a data-use agreement (research
log, 2026-10-19).
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

#: Key ids whose texts were committed beside their digests, so the digests can be paired with
#: them without the key. `load_key` refuses these; a published result is digested under a new key.
BURNED_KEY_IDS: Mapping[str, str] = {
    "659eedab9a778439": (
        "its texts are in git history beside its digests (research log, 2026-10-19)"
    ),
}

#: Fields holding model text conditioned on or distilled from review comments, wherever they sit.
#: Text from prompts without review text stays: the contamination battery's completions of code
#: and the distillations of written guides (`kind: guide`), which also carry `map_lists`.
ALWAYS = frozenset({"prediction", "predictions", "passes", "map_lists", "reduce_answer"})
#: Withheld only inside a review distillation: `file` is a path and `rules` a version elsewhere.
IN_DISTILLATION = frozenset({"rules", "file"})
_DISTILLATION_MARKS = frozenset(
    {"map_lists", "reduce_answer", "map_lists_hmac", "reduce_answer_hmac"}
)


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
    if (reason := BURNED_KEY_IDS.get(key_id(key))) is not None:
        raise SystemExit(
            f"{KEY_ENV} holds a burned key ({reason}). Generate a new one (see .env.example) "
            "and digest the next published result under it."
        )
    return key


def key_id(key: bytes) -> str:
    """Names the key a file's digests were made under without revealing it."""
    return hmac.new(key, b"sphragis withholding key id", hashlib.sha256).hexdigest()[:16]


def digest(text: str, key: bytes) -> str:
    """HMAC-SHA256 of a text's UTF-8 bytes, as hex."""
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def fields_for(node: Mapping[str, Any]) -> frozenset[str]:
    """The fields withheld in this object, by its own shape, never by the name of its file."""
    if node.get("kind") == "guide" and node.get("merge_chunks") is None:
        return ALWAYS - {"map_lists", "reduce_answer"}
    return ALWAYS | IN_DISTILLATION if _DISTILLATION_MARKS & node.keys() else ALWAYS


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


def text_left(document: Any) -> list[str]:
    """Where a document still carries withheld text, as JSON paths."""
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            fields = fields_for(node)
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
    count = 0

    def walk(node: Any) -> Any:
        nonlocal count
        if isinstance(node, Mapping):
            out: dict[str, Any] = {}
            fields = fields_for(node)
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
        "fields": sorted({p.rsplit("/", 1)[-1] for p in text_left(document)}),
        "texts": count,
        "algorithm": "HMAC-SHA256",
        "key_id": key_id(key),
        "full_text": "the cluster's results root, under a data-use agreement (docs/protocol.md)",
    }
    return result, count


def basis(documents: Mapping[str, Mapping[str, Any]]) -> str:
    """The suffix under which a reader compares the withheld fields of documents read together.

    `""` when every document carries text, `SUFFIX` when every one is digested under the same key
    id. A mix of published and plain copies, or of two keys, compares nothing and exits.
    """
    keys = {name: doc.get("withheld_text", {}).get("key_id") for name, doc in documents.items()}
    if len(set(keys.values())) == 1:
        return "" if None in keys.values() else SUFFIX
    if None in keys.values():
        raise SystemExit(f"cannot compare published and full-text copies together: {keys}")
    raise SystemExit(f"the copies are digested under different keys: {keys}")


def require_text(document: Mapping[str, Any], label: str) -> None:
    """Exit when a reader that needs model text is handed a published, withheld copy."""
    if "withheld_text" in document:
        raise SystemExit(
            f"{label} is a published copy: its {', '.join(document['withheld_text']['fields'])} "
            "are digests. Run this on the full-text copy in the cluster's results root."
        )
