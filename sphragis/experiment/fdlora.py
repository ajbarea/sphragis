"""FDLoRA's schedule (Lu et al., "FDLoRA: Personalized Federated Learning of Large Language Model
via Dual LoRA Tuning", arXiv:2406.07925): the pure round arithmetic and state averaging that
decide when the personalized module is overwritten and how much local training the global module
gets a round. `scripts/fdlora_schedule.py` is what drives a model with these; this module imports
none, so the schedule is testable without one.

Registered reading of Algorithm 1 is in `docs/research-log.md`, 2026-09-21 ("Registered before
computing: how we read FDLoRA's algorithm, and the step it does not account for").
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def should_sync(round_index: int, sync_period: int) -> bool:
    """Algorithm 1, line 9: the personalized module is overwritten every `sync_period` rounds.

    `round_index` is 0-based. The paper's `t` counts communication rounds from 1 and its printed
    condition is `is_sync <- t % H`: an integer, truthy whenever it is non-zero, so as written it
    fires on every round *except* a multiple of H -- backwards from the comment directly above it
    ("update the personalized LoRA module every H communication rounds"). The registered reading
    is `t % H == 0`, the one consistent with that comment, so `round_index + 1` stands in for `t`.
    """
    if sync_period < 1:
        raise ValueError(f"sync_period must be at least 1, got {sync_period}")
    return (round_index + 1) % sync_period == 0


def with_inner_steps(budget: Mapping[str, Any], inner_steps: int) -> dict[str, Any]:
    """FDLoRA's K: how many InnerOpt passes the global module takes before a round ends.

    Overrides only the registered training budget's epoch count for the call it is passed to;
    learning rate, batch size and the warmup ratio stay fixed across a K sweep, or the sweep
    would also be measuring the budget rather than K.
    """
    if inner_steps < 1:
        raise ValueError(f"inner_steps must be at least 1, got {inner_steps}")
    return {**budget, "epochs": inner_steps}


def average_state_dicts(state_dicts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Algorithm 1, line 7: `theta_s(0) <- (1/N) sum_i theta_p(i)`.

    The one round this reading says leaves every client once, and the paper's own accounting
    does not mention: an average over N clients needs the server to have seen each of them.

    Written over `+` and `/ int` only, and nothing else, so it averages plain numbers in a test
    here and real adapter tensors in the script that imports the GPU stack this module does not.
    """
    if not state_dicts:
        raise ValueError("average_state_dicts needs at least one state dict")
    n = len(state_dicts)
    averaged: dict[str, Any] = {}
    for key in state_dicts[0]:
        total = state_dicts[0][key]
        for state_dict in state_dicts[1:]:
            total = total + state_dict[key]
        averaged[key] = total / n
    return averaged
