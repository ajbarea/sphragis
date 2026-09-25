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


def round0_seed(personalized: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Algorithm 1, line 7's average, over every client label the caller passes in.

    Takes the one mapping keyed by every client across every source, so a caller cannot average
    one source's clients at a time by construction the way a bare `average_state_dicts` call
    over a per-source slice would.
    """
    return average_state_dicts(list(personalized.values()))


def validate_schedule(
    rounds: int, inner_steps: int, sync_period: int, *, allow_final_sync: bool
) -> None:
    """Refuse a bad R/K/H before Stage 1 trains anything.

    `rounds`, `inner_steps` and `sync_period` all read as counts and must be at least 1; checked
    here rather than left to `should_sync`/`with_inner_steps` so the message names the argument
    before either is called. The final-sync collision (`rounds` a multiple of `sync_period`) is
    the same refusal the script already carried, unless `allow_final_sync` says the collision
    (the synchronous `sync-period=1` endpoint) is intended.
    """
    if rounds < 1:
        raise SystemExit(f"rounds must be at least 1, got {rounds}")
    if inner_steps < 1:
        raise SystemExit(f"inner_steps must be at least 1, got {inner_steps}")
    if sync_period < 1:
        raise SystemExit(f"sync_period must be at least 1, got {sync_period}")
    if should_sync(rounds - 1, sync_period) and not allow_final_sync:
        raise SystemExit(
            f"rounds={rounds} is a multiple of sync_period={sync_period} (H): the "
            "final round syncs, so the saved -local would equal the transmitted global by "
            "construction. Pass --allow-final-sync if that is the point (the synchronous "
            "sync-period=1 endpoint), or choose rounds/sync_period so the last round does not "
            "coincide with a sync."
        )


def local_provenance(rounds: int, sync_period: int) -> dict[str, Any]:
    """What the saved `-local` module equals, under the registered reading of line 14.

    `-local` is overwritten with that round's transmitted upload on every sync round
    (`should_sync`), so its final saved value is the last sync's upload, or, when no round in
    `rounds` syncs at all, still Stage 1's personalized module (`-p0`). `round` is 1-based, the
    same count `should_sync`'s own docstring maps `round_index` onto.
    """
    last_sync = max((r for r in range(rounds) if should_sync(r, sync_period)), default=None)
    if last_sync is None:
        return {"equals": "p0"}
    return {"equals": "sync_upload", "round": last_sync + 1}
