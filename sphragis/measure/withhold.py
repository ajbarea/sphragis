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
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any, NamedTuple

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


#: An example's id is `{org}:{change}:{path}:{start}` (`corpus/examples.py`, `corpus/build.py`),
#: the change a Gerrit change id or a GitHub `repo#number`. Every id in the tracked results has a
#: 40-hex Gerrit change; the longest path with whitespace is five words.
_CHANGE = r"I[0-9a-fA-F]{40}"
_ID = re.compile(
    rf"(?P<org>[a-z0-9_-]+):(?:{_CHANGE}|[\w.\-]+/[\w.\-]+#\d+):(?P<path>.+?):\d+(?::\d+)?"
)
_PATH_WORDS = 5
#: The dtype runs a determinism check keys its passes by, and what each computes in
#: (`scripts/determinism_check.py` writes both).
PASS_DTYPES: Mapping[str, str] = {
    "bf16": "bfloat16",
    "bf16_repeat": "bfloat16",
    "bf16_fresh": "bfloat16",
    "fp32": "float32",
}
PASS_LABELS = frozenset(PASS_DTYPES)
#: Plain hashes of prompts or distillations, which a reader holding the public comments can
#: match as a dictionary: `rules_files` and `base_marks` (`scripts/retrieval_comparator.py`) and
#: the per-row prompt `fingerprint` (`sphragis/experiment/retrieval.py`).
PLAIN_HASHES = frozenset({"rules_files", "base_marks", "fingerprint"})
#: Names a run is saved under besides its org's and its host's: `qtfull` is `RUN_TAG` of the
#: full-pool Qt runs, `qtbase` a Qt project.
MANUAL_ALIASES: Mapping[str, str] = {"qtfull": "qt", "qtbase": "qt"}
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

DIGEST = re.compile(r"[0-9a-f]{64}")
#: A bot-audit list of short real review comments, which no published artifact carries, and the
#: one key a row of it has: `scripts/data_audit.py` writes `{"changes": n}`.
COMMENT_LISTS = frozenset({"candidate_unregistered_bots"})
COMMENT_ROW_KEYS = frozenset({"changes"})
#: Fields whose keys hold whitespace, from a survey of the tracked JSON: the field the key sits
#: under (empty at the top of a file), what it may be, and the script that writes it.
WHITESPACE_KEYS: tuple[tuple[str, str, str], ...] = (
    ("ai_trailer_values", r"(?:Assisted|Co-Authored|Generated)-By: [\w. -]{1,24}", "ai_trailers"),
    (
        "relations",
        "other organization and content|other organization, same content"
        "|same organization and content, other project|same organization, other content"
        "|same project",
        "client_attribution",
    ),
    ("separation", r"\w+ - \w+", "contamination_blind"),
    (
        "automated_comments_by_bot",
        "Qt QUIP-23 security review integration|Qt Sanity Bot|flake8 lint output",
        "data_audit",
    ),
    (
        "pairs",
        r"(?:human|[ABC])~[ABC](?: (?:first|second) half)?"
        r"(?: (?:outside_names|valid_vs_rest|on checked items))?(?: without slips)?",
        "label_audit_agreement",
    ),
    (
        "cuts",
        "attention, all four|mlp, all three|[a-z_]+ alone|pfadapter (?:kept local \\(v,o\\)"
        "|transmitted \\(q,k\\))",
        "module_split",
    ),
    ("", r"within:\w+:[\w/.\-]+ vs [\w/.\-]+", "separability"),
)
_WHITESPACE_KEYS = tuple((field, re.compile(rx)) for field, rx, _ in WHITESPACE_KEYS)
#: A string of this many words in an object that carries an example id is model text, whatever
#: its key is called; none of the tracked results has one outside the fields named below.
PROSE_VALUE_WORDS = 3
#: The contamination battery's completions of code and its targets, from prompts without review
#: text (`scripts/contamination_blind.py`); never allowed in a granted host's file.
BATTERY_FIELDS = frozenset({"predicted", "reference"})

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


def is_example_id(text: str) -> bool:
    """Whether a string is an example id: the pattern, and a path that is not a sentence."""
    if (m := _ID.fullmatch(text)) is None:
        return False
    path = m["path"]
    if not re.search(r"\s", path):
        return True
    return len(path.split()) <= _PATH_WORDS and (
        "/" in path or re.search(r"\.\w+$", path) is not None
    )


def id_path(text: str) -> str | None:
    """The file path inside an example id, None for a string that is not one."""
    return m["path"] if is_example_id(text) and (m := _ID.fullmatch(text)) else None


def _listed_ids(document: Any) -> frozenset[str]:
    """The ids the document itself lists: an `ids` list, and the `id` of any row."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "ids" and isinstance(v, list):
                    found.update(x for x in v if isinstance(x, str))
                elif k == "id" and isinstance(v, str):
                    found.add(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(document)
    return frozenset(found)


def _label(key: str, ids: frozenset[str]) -> bool:
    """A key that names a run or an example, not text: a dtype pass, an id the document lists,
    or an example id."""
    return key in PASS_LABELS or key in ids or is_example_id(key)


def _walk_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _walk_keys(v)


def _text_keys(value: Any, ids: frozenset[str]) -> list[str]:
    return [k for k in _walk_keys(value) if not _label(k, ids)]


def unlabelled_keys(value: Any, ids: frozenset[str]) -> list[str]:
    """Keys under a digest field that are neither labels nor digests."""
    return [k for k in _text_keys(value, ids) if not DIGEST.fullmatch(k)]


def _strings(value: Any) -> int:
    if isinstance(value, str):
        return 1
    if isinstance(value, dict):
        return sum(_strings(v) for v in value.values())
    if isinstance(value, list | tuple):
        return sum(_strings(v) for v in value)
    return 0


def _texts(value: Any, ids: frozenset[str]) -> int:
    return _strings(value) + len(_text_keys(value, ids))


def _digested(value: Any, key: bytes) -> Any:
    if isinstance(value, str):
        return digest(value, key)
    if isinstance(value, dict):
        return {k: _digested(v, key) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_digested(v, key) for v in value]
    return value


def text_left(document: Any) -> list[str]:
    """Where a document still carries withheld text, as JSON paths."""
    ids = _listed_ids(document)
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            fields = fields_for(node)
            for k, v in node.items():
                if k in fields and _texts(v, ids):
                    found.append(f"{path}/{k}")
                else:
                    walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}")

    walk(document, "")
    return found


@functools.cache
def _registry() -> Mapping[str, frozenset[str]]:
    """Each organization whose review host was opened by a written grant, with the host names it
    is read from, REST or git, from the registries the collector itself obeys."""
    from sphragis.corpus.cli import GERRIT, REST_PERMITTED, host_of
    from sphragis.corpus.notedb import GIT_HOSTS, GIT_PERMITTED

    found: dict[str, frozenset[str]] = {}
    for org in sorted(GERRIT.keys() | GIT_HOSTS.keys()):
        rest = REST_PERMITTED.get(host_of(org))
        git_host = urllib.parse.urlsplit(GIT_HOSTS.get(org, "")).hostname or ""
        git = GIT_PERMITTED.get(git_host)
        if (rest and rest.written_grant) or (git and git.written_grant):
            found[org] = frozenset(h for h in (host_of(org), git_host) if h)
    return found


def granted_orgs() -> frozenset[str]:
    """The organizations whose review hosts were opened by a written grant."""
    return frozenset(_registry())


@functools.cache
def aliases() -> Mapping[str, str]:
    """Other names a granted org is saved under: its hosts' first labels without `review`
    (`android-review` for `aosp`), and the manual ones."""
    found = dict(MANUAL_ALIASES)
    for org, hosts in _registry().items():
        for host in hosts:
            tokens = host.split(".")[0].split("-")
            label = "-".join(t for t in tokens if t not in {"review", "codereview"})
            if label and label != org:
                found[label] = org
    return found


def _alt(names: Iterable[str]) -> str:
    return "|".join(map(re.escape, sorted(names, key=len, reverse=True)))


@functools.cache
def _names() -> frozenset[str]:
    return granted_orgs() | aliases().keys()


@functools.cache
def _in_path() -> re.Pattern[str]:
    alt = _alt(_names())
    return re.compile(rf"(?:^|[-_.])(?:{alt})\d*(?:[-_.]|$)|foreign(?:{alt})")


@functools.cache
def _in_content() -> re.Pattern[str]:
    """A granted org's example id, an `org:` prefix, or one of its hosts' names."""
    hosts = {h for hs in _registry().values() for h in hs}
    alt = _alt(_names())
    return re.compile(rf"(?<!\w)(?:{alt}):{_CHANGE}:|^(?:{alt}):|{_alt(hosts)}", re.IGNORECASE)


def granted_path(rel: str) -> bool:
    """Whether any component of a path holds a granted org's name or alias at a token boundary,
    or glued after `foreign`, in any case."""
    return any(_in_path().search(part) for part in rel.lower().split("/"))


def granted_content(document: Any) -> bool:
    """Whether a document names a granted org: in a string or a key (its id, an `org:` prefix,
    one of its hosts), as a token of a key, or as the value of a field that says which
    organization it draws on."""
    names = _names()
    content = _in_content()

    def named(value: Any) -> bool:
        if isinstance(value, str):
            return value.lower() in names
        return isinstance(value, list) and any(named(v) for v in value)

    def walk(node: Any) -> bool:
        if isinstance(node, str):
            return content.search(node) is not None
        if isinstance(node, dict):
            return any(
                content.search(k) is not None
                or not names.isdisjoint(re.split(r"[^a-z0-9]+", k.lower()))
                or (k in ORG_FIELDS and named(v))
                or walk(v)
                for k, v in node.items()
            )
        return isinstance(node, list) and any(walk(v) for v in node)

    return walk(document)


def _digest_keys(document: Any) -> list[str]:
    """Where a document carries a digest of text or a plain hash of it, as JSON paths."""
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
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


def _counts_only(value: Any) -> bool:
    """The published shape of a comment list: rows of counts under the one key the audit writes."""
    return isinstance(value, list) and all(
        isinstance(row, dict)
        and set(row) <= COMMENT_ROW_KEYS
        and all(isinstance(n, int) and not isinstance(n, bool) for n in row.values())
        for row in value
    )


def _strings_of(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings_of(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings_of(v)


def _listed_key(field: str, key: str) -> bool:
    return any(f == field and rx.fullmatch(key) for f, rx in _WHITESPACE_KEYS)


def _unfit(node: Any, ids: frozenset[str], path: str = "", field: str = "") -> Iterator[str]:
    """Digest fields not holding digests under labels, comment lists holding comments, and keys
    of prose."""
    if isinstance(node, dict):
        for k, v in node.items():
            if re.search(r"\s", k) and not is_example_id(k) and not _listed_key(field, k):
                yield f"{path}/{k[:40]} is a key of prose"
            if k.endswith(SUFFIX) and (
                not all(DIGEST.fullmatch(t) for t in _strings_of(v)) or unlabelled_keys(v, ids)
            ):
                yield f"{path}/{k} is not a digest under labels"
            elif k in COMMENT_LISTS and not _counts_only(v):
                yield f"{path}/{k} carries comment text"
            else:
                yield from _unfit(v, ids, f"{path}/{k}", k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _unfit(v, ids, f"{path}/{i}", field)


@functools.cache
def _lexer_names() -> tuple[frozenset[str], re.Pattern[str]]:
    """What a row's `lexer` may say: a lexer the tokenizer uses, `text`, or its fallback note."""
    from pygments.lexers import get_lexer_by_name

    from sphragis.experiment.tokens import LEXED, UNTILED

    head, tail = UNTILED.split("{}")
    return (
        LEXED | {get_lexer_by_name("text").name},
        re.compile(re.escape(head) + r".{1,60}" + re.escape(tail)),
    )


def _carries(node: Mapping[str, Any]) -> bool:
    return any(is_example_id(k) for k in node) or any(
        isinstance(v, str) and is_example_id(v) for v in node.values()
    )


def _prose(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """(key, where) for each string of several words anywhere under an object that carries an
    example id, but a `lexer` the tokenizer writes and a `path` the row's own id names."""
    if isinstance(node, dict) and _carries(node):
        ids = [*node, *(v for v in node.values() if isinstance(v, str))]
        yield from _sweep(node, path, "", {id_path(x) for x in ids})
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _prose(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _prose(v, f"{path}/{i}")


def _sweep(node: Any, path: str, key: str, own: set[str | None]) -> Iterator[tuple[str, str]]:
    if isinstance(node, str):
        if len(node.split()) < PROSE_VALUE_WORDS or is_example_id(node):
            return
        names, fallback = _lexer_names()
        if (key == "lexer" and (node in names or fallback.fullmatch(node))) or (
            key == "path" and node in own
        ):
            return
        yield key, f"{path} holds prose"
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _sweep(v, f"{path}/{k}", k, own)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _sweep(v, f"{path}/{i}", key, own)


class Verdict(NamedTuple):
    unfit: list[str]
    granted: bool
    findings: list[str]
    pinned: bool


def examine(rel: str, raw: bytes, documents: Sequence[Any], pin_rel: str | None = None) -> Verdict:
    """What a file may not be published with, from one pass over its documents.

    `unfit`: shapes no published file has, granted host or not (model text, digests that are not
    digests, comment text, keys of prose, prose under an example). `granted` and `findings`: a
    file drawn from a granted host (by any component of `rel`, or its content) and what it
    carries that the grants exclude. `pinned`: it is a committed exception byte for byte at
    `pin_rel` (default `rel`), the blob taken only when it matters.
    """
    unfit: list[str] = []
    findings: list[str] = []
    prose: list[tuple[str, str]] = []
    granted = granted_path(rel)
    for i, document in enumerate(documents):
        ids = _listed_ids(document)
        left = text_left(document)
        unfit += [f"{i}{p}" for p in [*left, *_unfit(document, ids)]]
        prose += [(k, f"{i}{p}") for k, p in _prose(document)]
        findings += [f"{i}{p}" for p in [*left, *_digest_keys(document)]]
        granted = granted or granted_content(document)
    unfit += [p for k, p in prose if granted or k not in BATTERY_FIELDS]
    pinned = bool(granted and findings and BEFORE_THE_RULE.get(pin_rel or rel) == blob(raw))
    return Verdict(unfit, granted, findings, pinned)


def refusals(
    rel: str, raw: bytes, documents: Sequence[Any], pin_rel: str | None = None
) -> list[str]:
    """Why a file may not be published, empty when it may: the one predicate the guard and the
    script that digests a result both ask."""
    verdict = examine(rel, raw, documents, pin_rel)
    out = list(verdict.unfit)
    if verdict.granted and verdict.findings and not verdict.pinned:
        out += [f"granted host: {p}" for p in verdict.findings]
    return out


def withhold(document: Any, name: str, key: bytes) -> tuple[Any, int]:
    """The document with each withheld field's texts replaced by digests, and how many were.

    A field `f` holding text becomes `f_hmac` in the same place, its shape kept and each string
    in it digested; a field holding none (a prediction interval, a null) is left alone. A copy
    already withheld that still carries text is refused rather than half-merged.
    """
    count = 0
    ids = _listed_ids(document)

    def walk(node: Any) -> Any:
        nonlocal count
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            fields = fields_for(node)
            for k, v in node.items():
                if k in fields and _texts(v, ids):
                    if keys := _text_keys(v, ids):
                        raise ValueError(f"{name}: {k} holds text as keys ({keys[0][:30]!r})")
                    count += _strings(v)
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
