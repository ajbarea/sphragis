"""FDLoRA's schedule (arXiv:2406.07925): sync rounds, inner-step budgets, and the round-0 average.

Registered reading of Algorithm 1 is in `docs/research-log.md`, 2026-09-21 ("Registered before
computing"). These are pure round arithmetic and dict averaging, so they are tested without a
model: the schedule is what is under test, not the training it drives.
"""

from __future__ import annotations

import pytest

from sphragis.experiment.fdlora import average_state_dicts, should_sync, with_inner_steps


class TestShouldSync:
    def test_sync_period_one_fires_every_round(self) -> None:
        assert [should_sync(t, 1) for t in range(4)] == [True, True, True, True]

    def test_sync_period_three_fires_on_the_third_round_and_every_third_after(self) -> None:
        # 0-based round_index; the paper's t is 1-based, so round_index 2 is t=3.
        assert [should_sync(t, 3) for t in range(9)] == [
            False,
            False,
            True,
            False,
            False,
            True,
            False,
            False,
            True,
        ]

    def test_a_sync_period_longer_than_the_run_never_fires(self) -> None:
        assert [should_sync(t, 10) for t in range(9)] == [False] * 9

    def test_sync_period_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            should_sync(0, 0)

    def test_the_papers_own_printed_condition_is_not_what_is_implemented(self) -> None:
        """`is_sync <- t % H` (Algorithm 1, line 9) is truthy on every round except a multiple of
        H, backwards from the comment above it. The registered reading is `t % H == 0`, so this
        checks the two disagree on a case where the difference is visible: H=3's second round."""
        t = 1  # round_index 1 is t=2 (1-based); t % H for H=3 is 2, truthy under the paper's own
        # printed condition, so its literal pseudocode would sync here. The registered reading
        # does not.
        assert should_sync(t, 3) is False


class TestWithInnerSteps:
    def test_only_epochs_changes(self) -> None:
        budget = {"learning_rate": 2e-4, "epochs": 2, "batch_size": 16}
        assert with_inner_steps(budget, 5) == {"learning_rate": 2e-4, "epochs": 5, "batch_size": 16}

    def test_the_original_budget_is_not_mutated(self) -> None:
        budget = {"epochs": 2}
        with_inner_steps(budget, 5)
        assert budget == {"epochs": 2}

    def test_inner_steps_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            with_inner_steps({"epochs": 2}, 0)


class TestAverageStateDicts:
    def test_averages_elementwise(self) -> None:
        assert average_state_dicts([{"a": 1.0, "b": 4.0}, {"a": 3.0, "b": 8.0}]) == {
            "a": 2.0,
            "b": 6.0,
        }

    def test_one_state_dict_returns_itself(self) -> None:
        assert average_state_dicts([{"a": 5.0}]) == {"a": 5.0}

    def test_three_state_dicts(self) -> None:
        assert average_state_dicts([{"a": 1.0}, {"a": 2.0}, {"a": 3.0}]) == {"a": 2.0}

    def test_empty_input_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            average_state_dicts([])

    def test_a_missing_key_in_a_later_dict_raises(self) -> None:
        with pytest.raises(KeyError):
            average_state_dicts([{"a": 1.0, "b": 2.0}, {"a": 3.0}])

    def test_works_over_lists_of_numbers_not_only_scalars(self) -> None:
        # Torch tensors support `+` and `/ int` the same way; this checks the function assumes
        # nothing more than that, using a plain list-like stand-in instead of a real tensor.
        class Vector(list):
            def __add__(self, other):  # type: ignore[override]
                return Vector(a + b for a, b in zip(self, other, strict=True))

            def __truediv__(self, n):  # type: ignore[override]
                return Vector(a / n for a in self)

        averaged = average_state_dicts([{"a": Vector([2.0, 4.0])}, {"a": Vector([4.0, 8.0])}])
        assert list(averaged["a"]) == [3.0, 6.0]
