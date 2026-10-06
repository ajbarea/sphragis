"""The study's time windows, and where the sealed confirmatory test window starts.

Kept apart from `cli` so `notedb.collect`, which refuses to read past the seal before fetching
anything, and the experiment modules can read them without importing the collection CLI: `cli`
already imports from `notedb`.
"""

from __future__ import annotations

TEST_WINDOW_START = "2025-11-01"

# Window bounds are a study parameter, not a runtime flag: they are fixed in the Stage 1
# report and changing them after the fact would move the confirmatory set.
#
# The test window runs twelve months rather than ten. The gate is conjunctive, so its power
# is the probability BOTH organizations clear zero, and twelve months clears it more often than
# ten (the figures that chose it are superseded as absolute power; see the ROADMAP's registered
# decisions) for 0.4 points of additional
# differential censoring, still a twelfth of what the dev window carries. It is set now,
# before any test data exists to be seen: 2026-10 closes before the Stage 1 submission on
# 2026-11-20, and the window is still fetched only after in-principle acceptance.
WINDOWS = {
    "pilot": ("2024-10-01", "2024-11-01"),
    "train": ("2024-11-01", "2025-09-01"),
    "dev": ("2025-09-01", TEST_WINDOW_START),
    "test": (TEST_WINDOW_START, "2026-11-01"),
}
