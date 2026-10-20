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

import functools
import hashlib
import hmac
import os
import re
import urllib.parse
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
#: `sources` is the review text of a pool's rows (`scripts/rules_distil.py`).
IN_DISTILLATION = frozenset({"rules", "file", "sources"})
_DISTILLATION_MARKS = frozenset(
    {"map_lists", "reduce_answer", "map_lists_hmac", "reduce_answer_hmac"}
)


#: An example's id: `{org}:{change}:{path}:{start}` (`corpus/examples.py`, `corpus/build.py`), a
#: Gerrit change id or a GitHub `repo#number`; the path may hold spaces.
EXAMPLE_ID = re.compile(r"[a-z0-9_-]+:(?:I\w+|[\w.\-]+/[\w.\-]+#\d+):.*:\d+(?::\d+)?")
#: The dtype runs a determinism check keys its passes by (`scripts/determinism_check.py`).
PASS_LABELS = frozenset({"bf16", "bf16_fresh", "bf16_repeat", "fp32"})
#: Plain hashes of prompts or distillations, which a reader holding the public comments can
#: match as a dictionary: `rules_files` (`scripts/retrieval_comparator.py`) and the per-row
#: prompt `fingerprint` (`sphragis/experiment/retrieval.py`).
PLAIN_HASHES = frozenset({"rules_files", "fingerprint"})
#: Names a run is saved under besides its org's: `qtfull` is `RUN_TAG` of the full-pool Qt runs.
ALIASES: Mapping[str, str] = {"qtfull": "qt"}
#: Fields naming the organization a document draws on, by value or in a list.
ORG_FIELDS = frozenset(
    {
        "org",
        "orgs",
        "organization",
        "organizations",
        "foreign",
        "foreign_source",
        "source",
        "against",
    }
)

#: Committed before the rule, from Qt's records held from before its grant, their predictions
#: published as digests (research log, 2026-10-19): repo-relative path to git blob. A file at one
#: of these paths whose blob differs, or the same name elsewhere, is refused.
BEFORE_THE_RULE: Mapping[str, str] = {
    f"datasets/results/{name}.json": sha
    for name, sha in {
        "projects-qt-creator_qt-creator-qt_qtbase-n788": "cd8bb7204fd110ce1698e03c7eca44682f54174d",
        "projects-qt-creator_qt-creator-qt_qtbase": "0943385a1999fa6ddfc8408159482ba5ab874a2b",
        "projects-qt-creator_qt-creator-qt_qtdeclarative": "27b7301b46dfd749dcf60b717161c1cce2d6f806",  # noqa: E501
        "projects-qt_qtbase-qt_qtdeclarative": "da228aa578c5f4ccdd16f667579915ecd4b75c91",
        "rq1-placebo-qt-s2": "c5182279d420a7bd76558ac06bc13591b9645b43",
        "rq1-placebo-qt-s3": "d2be46fba81215ee85ff5332ff4537c5c94af434",
        "rq1-placebo-qt": "a988435d1e78379166dd51b5f7257c1bab266c24",
        "rq1-windows-qtfull-fp32-s2": "71af3ce8d7d7a69b0c29a1cdde5b9b039e0433e1",
        "rq1-windows-qtfull-fp32-s3": "020c0e9da67aeee65dcd41ed9383c358bfbcbba4",
        "rq1-windows-qtfull-fp32": "475d274184608577726bc6b0f44c7b60147f14c9",
        "rq1-windows-qtfull-s2": "19035534650ebb29a2bfccf2277f2efbf5f5c494",
        "rq1-windows-qtfull-s3": "97281aff14202278c53ed62403fcf866765aa450",
        "rq1-windows-qtfull": "b59fc0b2202b0aae6afd47b4cea5351e1f978c02",
        "rq1-pilot": "fe4466d6936dc0a921508c78fd694a7522a1ee18",
        "rq1-pilot-equalized": "9f84130f3d52d06eb3cd04902c0945b4a18e0b15",
        "rq1-pilot-fp32-pilot": "f15efac18acc4250019b90795e944e1c7c743264",
        "rq1-windows": "67aab7ed3f54da2dc14349118f57f6c9c63a3362",
        "rq1-windows-r256": "02335de11dea017fdb62c9ee86bb03ebc9a8f267",
        "rq1-windows-s2-r256": "d450398139801bc01d6b527ccdd643251f398c3c",
        "rq1-windows-s3-r256": "43641704bf8b73fc6be4ec5c4a2c75aff42631b8",
    }.items()
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
    if (reason := BURNED_KEY_IDS.get(key_id(key))) is not None:
        raise SystemExit(
            f"{KEY_ENV} holds a burned key ({reason}). Generate a new one (see .env.example) "
            "and digest the next published result under it."
        )
    return key


def key_id(key: bytes) -> str:
    """Names the key a file's digests were made under without revealing it."""
    return hmac.new(key, b"sphragis withholding key id", hashlib.sha256).hexdigest()[:16]


def blob(raw: bytes) -> str:
    """The git blob id of a file's bytes, as `git hash-object` prints it."""
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()


def digest(text: str, key: bytes) -> str:
    """HMAC-SHA256 of a text's UTF-8 bytes, as hex."""
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def fields_for(node: Mapping[str, Any]) -> frozenset[str]:
    """The fields withheld in this object, by its own shape, never by the name of its file."""
    if _written_guide(node):
        return ALWAYS - {"map_lists", "reduce_answer"}
    marked = str(node.get("kind")).lower() == "reviews" or _DISTILLATION_MARKS & node.keys()
    return ALWAYS | IN_DISTILLATION if marked else ALWAYS


def _written_guide(node: Mapping[str, Any]) -> bool:
    """The shape both tracked guides have: no merge, no answer, no evidence or recurrence from
    comments, no digest, one page list a chunk and one a rule."""
    chunks, parts, rules, pages = (
        node.get(k) for k in ("chunks", "part_pages", "rules", "rule_pages")
    )
    return (
        node.get("kind") == "guide"
        and node.get("merge_chunks") is None
        and node.get("reduce_answer") is None
        and node.get("evidence") == []
        and node.get("recurrence") == []
        and not any(k.endswith(SUFFIX) for k in node)
        and isinstance(chunks, int)
        and not isinstance(chunks, bool)
        and chunks > 0
        and isinstance(parts, list)
        and len(parts) == chunks
        and isinstance(rules, list)
        and isinstance(pages, list)
        and len(pages) == len(rules)
    )


def _label(key: str) -> bool:
    """A key that names a run or an example, not text: an example id or a dtype pass."""
    return key in PASS_LABELS or EXAMPLE_ID.fullmatch(key) is not None


def _texts(value: Any) -> int:
    if isinstance(value, str):
        return 1
    if isinstance(value, Mapping):
        return sum(_texts(v) + (not _label(k)) for k, v in value.items())
    if isinstance(value, list | tuple):
        return sum(_texts(v) for v in value)
    return 0


def _digested(value: Any, key: bytes) -> Any:
    if isinstance(value, str):
        return digest(value, key)
    if isinstance(value, Mapping):
        return {(k if _label(k) else digest(k, key)): _digested(v, key) for k, v in value.items()}
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


@functools.cache
def granted_orgs() -> frozenset[str]:
    """The organizations whose review hosts were opened by a written grant, from the registry."""
    from sphragis.corpus.cli import GERRIT, REST_PERMITTED

    return frozenset(
        org
        for org, url in GERRIT.items()
        if (permission := REST_PERMITTED.get(urllib.parse.urlsplit(url).hostname or ""))
        and permission.written_grant
    )


@functools.cache
def _names() -> tuple[str, ...]:
    return tuple(sorted(granted_orgs() | ALIASES.keys(), key=len, reverse=True))


@functools.cache
def _in_path() -> re.Pattern[str]:
    names = "|".join(map(re.escape, _names()))
    return re.compile(rf"(?:^|[-_.])(?:{names})(?:[-_.]|$)|foreign(?:{names})")


@functools.cache
def _in_id() -> re.Pattern[str]:
    return re.compile(rf"(?<!\w)(?:{'|'.join(map(re.escape, granted_orgs()))}):I[0-9a-f]{{40}}:")


def granted_path(rel: str) -> bool:
    """Whether any component of a repo-relative path holds a granted org's name or alias at a
    token boundary, or glued after `foreign`, in any case."""
    return any(_in_path().search(part) for part in rel.lower().split("/"))


def granted_content(document: Any) -> bool:
    """Whether a document holds a granted org's example id, as a key or a value, or names the
    org in a field that says which organization it draws on."""

    def named(value: Any) -> bool:
        if isinstance(value, str):
            return value.lower() in granted_orgs() | ALIASES.keys()
        return isinstance(value, list) and any(named(v) for v in value)

    def walk(node: Any) -> bool:
        if isinstance(node, str):
            return _in_id().search(node) is not None
        if isinstance(node, Mapping):
            return any(
                _in_id().search(k) is not None or (k in ORG_FIELDS and named(v)) or walk(v)
                for k, v in node.items()
            )
        return isinstance(node, list) and any(walk(v) for v in node)

    return walk(document)


def granted_findings(document: Any) -> list[str]:
    """What a granted host's document may not carry: model text, any digest of it, and plain
    hashes of it. Where each is, as JSON paths."""
    found = text_left(document)

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():
                if k == "withheld_text" or k.endswith(SUFFIX) or k in PLAIN_HASHES:
                    found.append(f"{path}/{k}")
                else:
                    walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}")

    walk(document, "")
    return found


def pinned(rel: str, raw: bytes) -> bool:
    """Whether the bytes at a repo-relative path are the committed exception's."""
    return BEFORE_THE_RULE.get(rel) == blob(raw)


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
