"""Extract Qt's Sanity Bot message templates from the hook that writes them.

Qt's Sanity Bot posts the complaints of `git-hooks/sanitize-commit` in qt/qtrepotools as inline
Gerrit comments. Run against Gerrit (`$gerrit_rest`), a style failure or any complaint at level
-1 is posted as "Hint: <message>" and every other complaint as the bare message (the "(key ...)"
suffix is added only outside Gerrit). So the automated comments are exactly the message
arguments of `complain`, `complain_ln`, `complain_cln`, `styleFail` and `do_complain`, and
this reads them from the hook itself rather than from a list someone typed.

Each message becomes an anchored regular expression: string literals are escaped, and Perl
interpolations (`$word`, `$1`) and concatenated expressions (`formatSize($size)`) become a
wildcard bounded to one line, so a template cannot swallow a reviewer's paragraph after it.

The bot's wording changes over time, so the registry is the union over every version of the hook
in effect across a span: the version current at `--since` and every commit touching the hook up
to now. Each template records the versions it appears in.

    git clone https://github.com/qt/qtrepotools.git /tmp/qtrepotools
    uv run --no-sync --no-active python scripts/extract_bot_templates.py \\
        --repo /tmp/qtrepotools --since 2024-10-01 \\
        --out sphragis/corpus/automated/qt-sanity-bot.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from sphragis.provenance import provenance_header

CALLS = ("complain_cln", "complain_ln", "complain", "styleFail", "do_complain")
WILD = "[^\\n]+?"

# check_spelling posts "$word -> $correction?$sfx": one lowercased dictionary word, its
# correction, and " [*]" when the correction is the American spelling. Read as a bare wildcard it
# would match any reviewer's "a -> b?", so it is pinned to the shape the code writes.
SPELLING = (r"\$word -> \$correction\?\$sfx", r"[a-z']+ \-> [A-Za-z' \-]+\?(?: \[\*\])?")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", type=Path, required=True, help="a full clone of qt/qtrepotools")
parser.add_argument("--since", required=True, help="YYYY-MM-DD: the corpus span's first day")
parser.add_argument("--out", type=Path, required=True)


def first_argument(source: str, start: int) -> str:
    """The text of a call's first argument, from just after its opening parenthesis."""
    depth, i, quote = 0, start, None
    while i < len(source):
        ch = source[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                return source[start:i]
            depth -= 1
        elif ch == "," and depth == 0:
            return source[start:i]
        i += 1
    raise ValueError(f"unterminated call at offset {start}")


def to_pattern(expression: str) -> str | None:
    """A Perl string expression as a regex body, or None if it holds no literal text."""
    parts, literal_seen = [], False
    for piece in re.finditer(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|([^"\']+)', expression):
        double, single, other = piece.groups()
        if double is not None:
            literal_seen = True
            text = re.sub(r"\\(.)", r"\1", double)
            chunks = re.split(r"\$\{?\w+\}?(?:\[[^\]]*\]|\{[^}]*\})*", text)
            parts.append(WILD.join(re.escape(c) for c in chunks))
        elif single is not None:
            literal_seen = True
            parts.append(re.escape(single))
        elif other.strip(" .\n\t"):
            parts.append(WILD)
    if not literal_seen:
        return None
    body = "".join(parts)
    while WILD + WILD in body:
        body = body.replace(WILD + WILD, WILD)
    return body


def extract(source: str) -> list[dict[str, str]]:
    templates: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"\b(" + "|".join(CALLS) + r")\s*\(", source):
        name = match.group(1)
        argument = first_argument(source, match.end())
        if name == "do_complain":
            # do_complain(line, msg, key, level): the message is the second argument.
            rest = source[match.end() + len(argument) + 1 :]
            argument = first_argument(rest, 0)
        if argument.strip() == '"' + SPELLING[0].replace("\\", "") + '"':
            templates.setdefault(SPELLING[1], {"pattern": SPELLING[1], "call": name})
            continue
        body = to_pattern(argument)
        if body is None or body.strip(re.escape(" ")) in ("", WILD):
            continue
        # A wildcard at the start is an interpolation such as do_complain rewriting its own
        # message ("$msg (key ...)"), not text the bot posts.
        if body.startswith(WILD):
            continue
        templates.setdefault(body, {"pattern": body, "call": name})
    return sorted(templates.values(), key=lambda t: t["pattern"])


HOOK = "git-hooks/sanitize-commit"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def versions(repo: Path, since: str) -> list[tuple[str, str]]:
    """(commit, date) of the hook version in effect on `since` and of every later change."""
    first = _git(repo, "log", "-1", "--format=%H %cs", f"--before={since}", "--", HOOK).split()
    later = [
        line.split()
        for line in _git(
            repo, "log", "--format=%H %cs", f"--since={since}", "--", HOOK
        ).splitlines()
    ]
    found = ([tuple(first)] if first else []) + [tuple(v) for v in reversed(later)]
    return [(c, d) for c, d in found]


def main() -> None:
    args = parser.parse_args()
    union: dict[str, dict] = {}
    span = versions(args.repo, args.since)
    for commit, _ in span:
        for template in extract(_git(args.repo, "show", f"{commit}:{HOOK}")):
            entry = union.setdefault(template["pattern"], {**template, "versions": []})
            entry["versions"].append(commit[:12])
    templates = sorted(union.values(), key=lambda t: t["pattern"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "bot": "Qt Sanity Bot",
                "source": "https://github.com/qt/qtrepotools/blob/<version>/" + HOOK,
                "versions": [{"commit": c, "date": d} for c, d in span],
                "since": args.since,
                "prefixes": ["Hint: "],
                "templates": templates,
                "provenance": provenance_header(),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"{len(templates)} templates over {len(span)} hook versions -> {args.out}")


if __name__ == "__main__":
    main()
