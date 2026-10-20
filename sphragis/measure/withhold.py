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
#: the per-row prompt `fingerprint` (`sphragis/experiment/retrieval.py`). The cluster has no key
#: and keeps them plain to resume; `withhold` re-keys them as `<field>_hmac` for publication.
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


#: Committed before the rule, with plain SHA-256 hashes of prompts or distilled rules in the fields
#: of `PLAIN_HASHES`: their hashes are in git history, which is not rewritten, and keying them
#: now would tie the new key to texts whose plain hashes are public. Repo-relative path to git
#: blob, held to the same terms as `BEFORE_THE_RULE`. A published result carries them as digests.
PLAIN_HASHES_BEFORE_THE_RULE: Mapping[str, str] = {
    f"datasets/results/{name}.json": sha
    for name, sha in {
        "retrieval-foreign-openstack-rp1.0": "54118ae6fce38cae983e53c0591529cc11ae35f5",
        "retrieval-foreign-openstack": "173030aab55803d0fb43702f04d5485f88bd2d56",
        "retrieval-foreign-wikimedia-rp1.0": "d148dc927ad9f8f0b2551dd7b78214c479792de6",
        "retrieval-foreign-wikimedia": "0b8524db7c3eb396d4941eebfaf82fbb9c6d4c7f",
        "retrieval-halves-openstack-p10-rp1.0": "ffe04144f02d3a111d2994364bd36c58233fc86c",
        "retrieval-halves-openstack-p10": "91d4b4fbefb883b31ca7bcd28a484d94aa454371",
        "retrieval-halves-openstack-p11-rp1.0": "cbb88b2e9424f89c01fa9ee0948d80cfe2246cd3",
        "retrieval-halves-openstack-p11": "5ed81126e2db8d70019bbff32ad6a18773c21fe7",
        "retrieval-halves-openstack-p12-rp1.0": "89ec6c1bedf1b579ec6001833a9c0574ac593db9",
        "retrieval-halves-openstack-p12": "76fc6f5d2a5932e67f4facdd3de797a19485fbb9",
        "retrieval-halves-openstack-p2-rp1.0": "d691b114f633d99efb7f97ef3605180bd35f0041",
        "retrieval-halves-openstack-p2": "b442971d36e6e5a8e84321e7ae278a6d8169a950",
        "retrieval-halves-openstack-p3-rp1.0": "d0f83030616da6558ca21b271d8d8e44875d20f7",
        "retrieval-halves-openstack-p3": "793b27d3247630c835311ffbb8afc1ef6371dae5",
        "retrieval-halves-openstack-p4-rp1.0": "02e3965bb17791b3814b43076c5d5b7a61f50381",
        "retrieval-halves-openstack-p4": "bb89f1588868f966f7307aaa1a382aaa34022187",
        "retrieval-halves-openstack-p5-rp1.0": "c5dd512384c8729455bf6f66cc4f72bd6f12c18d",
        "retrieval-halves-openstack-p5": "5db2964045bb9832e3f48c1f67021c09fa2d4436",
        "retrieval-halves-openstack-p7-rp1.0": "1b6475ac1d94cae3a7cdd635a97d96e7a6ef40d5",
        "retrieval-halves-openstack-p7": "389db144dd97a262a4162a3816716b7d0a02ab01",
        "retrieval-halves-openstack-p8-rp1.0": "cef5e758c3d577daded9cb074ffa902f5352633a",
        "retrieval-halves-openstack-p8": "52c1a4d6405bb65c183007f131422ac2827fad4f",
        "retrieval-halves-openstack-p9-rp1.0": "994a5aa9e9bfd813c27235429d9434479786ae5f",
        "retrieval-halves-openstack-p9": "6b389675e1487b184ceef3554e588ee7372fd73e",
        "retrieval-halves-wikimedia-p1-rp1.0": "3810f6be5c437fa89f288203d942d2dbf01b2627",
        "retrieval-halves-wikimedia-p1": "e1d38a5389e47bfa6657aeb997cec906b7da2e1f",
        "retrieval-halves-wikimedia-p10-rp1.0": "7eefbd0900c60eda4687747199b43b3895a31349",
        "retrieval-halves-wikimedia-p10": "f7d2730fb5b24224873be4faf61886d554087b1b",
        "retrieval-halves-wikimedia-p11-rp1.0": "c8f7c42f3cd2fed9a71a84c4fdb87152f006b72b",
        "retrieval-halves-wikimedia-p11": "a234a396686a81ab6f0c1697858f7b140841bb4e",
        "retrieval-halves-wikimedia-p2-rp1.0": "fa3176206e9327ef7a7a5c6170cceb8794320516",
        "retrieval-halves-wikimedia-p2": "a2bd66bb6d8d65dc64f1c08636813c4f4bca6f84",
        "retrieval-halves-wikimedia-p3-rp1.0": "a03c3baee2d283d0a991506f18f18c7ec7e629cf",
        "retrieval-halves-wikimedia-p3": "7799b9aaf8fa70ce64e1273ee64ed8ecbce44b43",
        "retrieval-halves-wikimedia-p5-rp1.0": "d9e8edbc71c8a9f8427c771e068ecebae5eb6eab",
        "retrieval-halves-wikimedia-p5": "6930559e7df60233a6fb6000420cf1379faff0ff",
        "retrieval-halves-wikimedia-p6-rp1.0": "7a52b3d6f434f6395b9a37f5f17012e49b8deae6",
        "retrieval-halves-wikimedia-p6": "8dd2ab3b99782ab3e41ef46d2434624f083e7f9c",
        "retrieval-halves-wikimedia-p7-rp1.0": "144750a26fee0fada510db8cedb387f927eceb7a",
        "retrieval-halves-wikimedia-p7": "187a3a3ba087bfd25a5c9dfe6dabaee1924d160d",
        "retrieval-halves-wikimedia-p8-rp1.0": "d31f86e7ff7126ca1006802743b5dbb4485ef83e",
        "retrieval-halves-wikimedia-p8": "d897e1622d3843ec5bbab842a71681f8b7127f39",
        "retrieval-halves-wikimedia-p9-rp1.0": "97ed74d4996f4f1f62365669b79dfa17d798b2a9",
        "retrieval-halves-wikimedia-p9": "e5b6e566f32d13cb5d15d0a4fdfb58e2468202e8",
        "rules-foreign-openstack-rp1.0": "e1faaf1666318cddcdc7ad7c912c79886c201cb5",
        "rules-foreign-openstack": "25f364b9644534a1c648692048b13045e4c53a50",
        "rules-foreign-wikimedia-rp1.0": "f525e0cf32c29e92300df9dfaf3afb64cf6a53dd",
        "rules-foreign-wikimedia": "3490d2b5defc4d2b33e52efc0db786ed3d6192c1",
        "rules-guide-openstack": "6dd1dba5791ae59d5b95261cfdeba6ec837af72d",
        "rules-guide-wikimedia": "305793424ea704151ce7473435723f79c92dcb93",
        "rules-halves-openstack-p10-rp1.0": "fcc3e228540a28650bd63ceec4631674d14184c0",
        "rules-halves-openstack-p10": "de26341bb7214354049efd02c639ac3dc06b2d87",
        "rules-halves-openstack-p11-rp1.0": "e8d69d0684faafd6129b65634517c8896ba07a66",
        "rules-halves-openstack-p11": "e0eeadd0605cff373c327b0a8404e4e70304691b",
        "rules-halves-openstack-p12-rp1.0": "ce9ea998a53c8ff507baff5634d2d0689988ac97",
        "rules-halves-openstack-p12": "a66e03059f6bedcf7611ab2a4df653c21e46f341",
        "rules-halves-openstack-p2-rp1.0": "20fe7ca565e83332299d58c0b2c0e9fa8df9168c",
        "rules-halves-openstack-p2": "faf3c4756b32803c0477597d13e7812bd4abd335",
        "rules-halves-openstack-p3-rp1.0": "82baef989566ba6325c213ff02498decf26a1572",
        "rules-halves-openstack-p3": "29cdac5b5384b31688f0d73dea0d819616079b7d",
        "rules-halves-openstack-p4-rp1.0": "0494901940228cca117f138e85f19c06e76c8f69",
        "rules-halves-openstack-p4": "2fd8d178ed574890ee4b54831d5c01f63c05aed9",
        "rules-halves-openstack-p5-rp1.0": "1122c6314b84344c8ddad3e936c6f75069d7c747",
        "rules-halves-openstack-p5": "e1208a332ff56c158ffbc23f02da08bb59b6b487",
        "rules-halves-openstack-p7-rp1.0": "2da8a6a86eab59fbeca23ced2755e54d90ac53c4",
        "rules-halves-openstack-p7": "632732da91b52ff4afa918f66cd441e715ab1411",
        "rules-halves-openstack-p8-rp1.0": "caea8a4281460062159452a3488fffa421cd0846",
        "rules-halves-openstack-p8": "5b605ee0ae8477c3f7909752f6709c92614ff9b7",
        "rules-halves-openstack-p9-rp1.0": "c7e2ea5126bd75d846681b2699a226eacbae3eca",
        "rules-halves-openstack-p9": "fd3b364aa79906735457f8f43728b36d890cfdbb",
        "rules-halves-wikimedia-p1-rp1.0": "9392ab4b3dd45e72cf12fa9384efd63200647ca8",
        "rules-halves-wikimedia-p1": "baa2313890f3354c9c1db72ca5c78d78934a1747",
        "rules-halves-wikimedia-p10-rp1.0": "c7cfdba86d377a79b311639620dbe13fe7a20073",
        "rules-halves-wikimedia-p10": "cc494b633e3838794d0e5aeeedf048e70194f378",
        "rules-halves-wikimedia-p11-rp1.0": "9eee3dbe7df5d5358a0328cab76d87377cfa5e6d",
        "rules-halves-wikimedia-p11": "c0850015bfd09d09c0a21e6198ae0098d68eba6e",
        "rules-halves-wikimedia-p2-rp1.0": "4cf0a6aa4fd2716569af52affc3977ae9612b99b",
        "rules-halves-wikimedia-p2": "a4582380e8de9c2c87ca234542f9ad2425958964",
        "rules-halves-wikimedia-p3-rp1.0": "e0db18c1ad235f34a528e846a2838d1f467a75b1",
        "rules-halves-wikimedia-p3": "33148c36d80409d62f5eb492b0429b26c27cf320",
        "rules-halves-wikimedia-p5-rp1.0": "82d3f62527837aebdc205f75e25f76bf64ae25af",
        "rules-halves-wikimedia-p5": "4b67d5d0e515eabbde955474db148d0252a14766",
        "rules-halves-wikimedia-p6-rp1.0": "764a7fb8ad733e2fad47111444cf80227e591456",
        "rules-halves-wikimedia-p6": "ce551933f64f233cf3bf5a74fcdde43df3889435",
        "rules-halves-wikimedia-p7-rp1.0": "39a9474e9af085498aefd9bcc5a7cb793173eeac",
        "rules-halves-wikimedia-p7": "a3664f84faeb2245880ff3cccdb0e4b5aa6b9759",
        "rules-halves-wikimedia-p8-rp1.0": "a01f55aa2e444920eff6d00c412bec3485239067",
        "rules-halves-wikimedia-p8": "96c7b38d8d3d93e9c62d3762b41ae50a48f8f5df",
        "rules-halves-wikimedia-p9-rp1.0": "2296eff88d05012615c0188658ee0cd31b776d38",
        "rules-halves-wikimedia-p9": "a33e3d51b708269be80bfef0b3b9877fbe91c81e",
        "rules-reviews-openstack-p10": "63db8e4e78fa2617c49380326ff80043de1c7043",
        "rules-reviews-openstack-p11": "36b298f4ab97891aa5088814c1e36466f4f63f09",
        "rules-reviews-openstack-p12": "5f1dd1640897cec7552ecdf7901a82d546b74018",
        "rules-reviews-openstack-p2-smoke.openstack-a.part": "757e2bd61683763f6e9b0a10d7345322a76a5695",  # noqa: E501
        "rules-reviews-openstack-p2-smoke2": "c2bc6d135e84c4519fdaf0bf20f5b2cbd059e102",
        "rules-reviews-openstack-p2-smoke3": "e5a0928c3d4b7310eebfedfb6b6d41eea5707cd0",
        "rules-reviews-openstack-p2-smoke4": "42443833d29999304087684206f22d9e12685706",
        "rules-reviews-openstack-p2-smoke5": "8a13459de8af03bd90c63003cc1dd9f13313e63b",
        "rules-reviews-openstack-p2": "245cbc1ea4372fff546f67e53f887489f90e8c65",
        "rules-reviews-openstack-p3": "2ab672a31bb33ad0e1486505bbba8318a35f7ef5",
        "rules-reviews-openstack-p4": "eae86a9cbf15daa98fb8805d0278ada219f10bc9",
        "rules-reviews-openstack-p5": "f335f74b503a18e86f38ac3bacf2ca885c07d344",
        "rules-reviews-openstack-p7": "3af549ec7e9214d0eb3b7def11828d3ff1256214",
        "rules-reviews-openstack-p8": "04c00000c5d9b1ec3f1e52acc58d9f039b09b264",
        "rules-reviews-openstack-p9": "b006dcb70243c1315e49ed0c8890313b320c1dab",
        "rules-reviews-wikimedia-p1-smoke2": "9bec16971634751cb6ece527849b20d293ec5edc",
        "rules-reviews-wikimedia-p1-smoke3": "3fa1595d1298b4ee5177c75e6a0e11560663b680",
        "rules-reviews-wikimedia-p1-smoke4": "f87677db64cceab5807faf6998f01106ed718ceb",
        "rules-reviews-wikimedia-p1-smoke5": "a75218156942f6693c399c38e336b7aeba1da34e",
        "rules-reviews-wikimedia-p1-smoke6": "b2d31ce259e74468babd03edb379d465cad96cc8",
        "rules-reviews-wikimedia-p1": "26db6c7b7c8510c896b3e7012b412e9962901e39",
        "rules-reviews-wikimedia-p10": "a4ff3d995cd00075a858d4ea59a290d7801b1a59",
        "rules-reviews-wikimedia-p11": "0c52b80eea96da31b771b64894fd0468bc1a4cd3",
        "rules-reviews-wikimedia-p2": "3cea68ff64717fdc8aaf4e88c0645575f8645d5d",
        "rules-reviews-wikimedia-p3": "a6db63e0585ac71a73b7b88af0ee67a61d49990f",
        "rules-reviews-wikimedia-p5": "6778267e35206d47a184aef749bd5a020bfa4e48",
        "rules-reviews-wikimedia-p6": "fbc0a6542276a32f60028e3a94df0ff2a2c1e572",
        "rules-reviews-wikimedia-p7": "ed434e932f88f0666156569a1b586a41d5b4213a",
        "rules-reviews-wikimedia-p8": "d7918898bd6c93c1a0b474b60125a31ae1b37886",
        "rules-reviews-wikimedia-p9": "c2439a3b91afadef6d917609a4ee7e4c2b2c6dd8",
    }.items()
}  # fmt: skip


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


@functools.cache
def _rules_file_key() -> re.Pattern[str]:
    """A key of `rules_files`: `<condition>:<owner>`, the owner an organization or one half."""
    from sphragis.experiment.rules_arms import DISTILLED, WRITTEN

    return re.compile(rf"(?:{re.escape(DISTILLED)}|{re.escape(WRITTEN)}):[a-z0-9]+(?:-[a-z0-9]+)*")


def _label(key: str, ids: frozenset[str]) -> bool:
    """A key that names a run or an example, not text: a dtype pass, an id the document lists,
    an example id, or the name of a rules file."""
    return (
        key in PASS_LABELS
        or key in ids
        or is_example_id(key)
        or _rules_file_key().fullmatch(key) is not None
    )


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


def plain_left(document: Any) -> list[str]:
    """Where a document still carries a plain hash field (`PLAIN_HASHES`) holding a string, as
    JSON paths."""
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in PLAIN_HASHES:
                    if _strings(v):
                        found.append(f"{path}/{k}")
                else:
                    walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}")

    walk(document, "")
    return found


def _rekeyed(field: str, value: Any, key: bytes) -> tuple[Any, int]:
    """A plain hash field with each hash replaced by the HMAC of its hex string, and how many.

    One hash, or a flat dict or list of them; anything else is refused rather than guessed at.
    """

    def hashed(v: Any) -> bool:
        return isinstance(v, str) and DIGEST.fullmatch(v) is not None

    if hashed(value):
        return digest(value, key), 1
    if isinstance(value, dict) and all(hashed(v) for v in value.values()):
        return {k: digest(v, key) for k, v in value.items()}, len(value)
    if isinstance(value, list) and all(hashed(v) for v in value):
        return [digest(v, key) for v in value], len(value)
    raise ValueError(f"{field} is not a SHA-256 hex digest or a dict or list of them")


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
    plain: list[str]
    plain_pinned: bool


def pinned(pins: Mapping[str, str], rel: str, raw: bytes) -> bool:
    """Whether a file is one of the pinned exceptions, byte for byte, at this path."""
    return pins.get(rel) == blob(raw)


def examine(rel: str, raw: bytes, documents: Sequence[Any], pin_rel: str | None = None) -> Verdict:
    """What a file may not be published with, from one pass over its documents.

    `unfit`: shapes no published file has, granted host or not (model text, digests that are not
    digests, comment text, keys of prose, prose under an example). `granted` and `findings`: a
    file drawn from a granted host (by any component of `rel`, or its content) and what it
    carries that the grants exclude. `pinned`: it is a committed exception byte for byte at
    `pin_rel` (default `rel`), the blob taken only when it matters. `plain`: where it carries a
    plain hash field, `plain_pinned`: it is one of the files committed so before the rule.
    """
    unfit: list[str] = []
    findings: list[str] = []
    plain: list[str] = []
    prose: list[tuple[str, str]] = []
    granted = granted_path(rel)
    for i, document in enumerate(documents):
        ids = _listed_ids(document)
        left = text_left(document)
        unfit += [f"{i}{p}" for p in [*left, *_unfit(document, ids)]]
        prose += [(k, f"{i}{p}") for k, p in _prose(document)]
        findings += [f"{i}{p}" for p in [*left, *_digest_keys(document)]]
        plain += [f"{i}{p}" for p in plain_left(document)]
        granted = granted or granted_content(document)
    unfit += [p for k, p in prose if granted or k not in BATTERY_FIELDS]
    at = pin_rel or rel
    return Verdict(
        unfit,
        granted,
        findings,
        bool(granted and findings and pinned(BEFORE_THE_RULE, at, raw)),
        plain,
        bool(plain and pinned(PLAIN_HASHES_BEFORE_THE_RULE, at, raw)),
    )


def refusals(
    rel: str, raw: bytes, documents: Sequence[Any], pin_rel: str | None = None
) -> list[str]:
    """Why a file may not be published, empty when it may: the one predicate the guard and the
    script that digests a result both ask."""
    verdict = examine(rel, raw, documents, pin_rel)
    out = list(verdict.unfit)
    if verdict.granted and verdict.findings and not verdict.pinned:
        out += [f"granted host: {p}" for p in verdict.findings]
    if verdict.plain and not verdict.plain_pinned:
        out += [f"plain hash of generated text: {p}" for p in verdict.plain]
    return out


def withhold(document: Any, name: str, key: bytes) -> tuple[Any, int]:
    """The document with each withheld field's texts replaced by digests, and how many were.

    A field `f` holding text becomes `f_hmac` in the same place, its shape kept and each string
    in it digested; a field holding none (a prediction interval, a null) is left alone. A plain
    hash field (`PLAIN_HASHES`) becomes `f_hmac` too, each hash replaced by the HMAC of its hex
    string, so the cluster's resume keeps its plain hashes and the published copy holds none. A
    copy already withheld that still carries text is refused rather than half-merged.
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
                elif k in PLAIN_HASHES:
                    if f"{k}{SUFFIX}" in node:
                        raise ValueError(f"{name}: {k} sits beside {k}{SUFFIX}")
                    try:
                        out[f"{k}{SUFFIX}"], n = _rekeyed(k, v, key)
                    except ValueError as error:
                        raise ValueError(f"{name}: {error}") from error
                    count += n
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
        "fields": sorted(
            {p.rsplit("/", 1)[-1] for p in [*text_left(document), *plain_left(document)]}
        ),
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
