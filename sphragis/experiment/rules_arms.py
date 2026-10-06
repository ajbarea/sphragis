"""The rules-file comparator's arms: a rules file in the system turn, the registered prompt after.

Apart from the distillation (`rules.py`) so a change here, on the side that prompts and scores
the arms, does not change the fingerprint every distilled file is checked against.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sphragis.experiment.grid import conditioned
from sphragis.experiment.runner import build_prompt
from sphragis.experiment.training import render_chat

# The model's own default system prompt (its chat template, read 2026-10-06). The rules arm
# keeps it and adds the file after it, so the arm differs from the base arm only by the file.
DEFAULT_SYSTEM = "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."


def rules_system(rules_file: str) -> str:
    """The system turn of a rules arm: the model's default, then the rules file."""
    return f"{DEFAULT_SYSTEM}\n\nFollow these conventions of the organization:\n{rules_file}"


def rules_prompt(example: Mapping[str, Any], rules_file: str) -> list[dict[str, str]]:
    """A rules arm's prompt: the file in the system turn, the registered prompt as the user's."""
    return [
        {"role": "system", "content": rules_system(rules_file)},
        {"role": "user", "content": build_prompt(example)},
    ]


# A distilled file is conditioned on a half, as an adapter is; a written one on an organization.
DISTILLED = "rules-distilled"
WRITTEN = "rules-written"


def rules_key(condition: str, owner: str, evaluated: str) -> str:
    """`<condition>:<owner>|<evaluated>`: the adapter arms' key, the file's owner in the adapter's
    place, with no training seed."""
    return conditioned(condition, owner, evaluated)


def rules_arms(
    targets: Sequence[Mapping[str, Any]],
    files: Mapping[tuple[str, str], str],
    *,
    evaluated: str,
) -> dict[str, list[list[dict[str, str]]]]:
    """Every target's prompt under every rules file, keyed as `rules_key`.

    `files` maps (condition, owner) to the file: (`DISTILLED`, half) or (`WRITTEN`, organization).
    """
    return {
        rules_key(condition, owner, evaluated): [rules_prompt(t, text) for t in targets]
        for (condition, owner), text in files.items()
    }


def arms_fingerprint() -> str:
    """This module's source, hashed: the arm prompts a rules job ran, held equal across jobs."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def default_system_holds(tokenizer: Any) -> bool:
    """Whether the model's chat template, given no system turn, renders `DEFAULT_SYSTEM`: so a
    rules arm, which writes the default then the file, differs from the base arm by the file."""
    implicit = render_chat(tokenizer, "U")
    explicit = render_chat(
        tokenizer, [{"role": "system", "content": DEFAULT_SYSTEM}, {"role": "user", "content": "U"}]
    )
    return implicit == explicit
