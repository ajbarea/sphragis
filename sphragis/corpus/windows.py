"""The one date the seal check needs: where the confirmatory test window starts.

Kept apart from `cli.WINDOWS` (the full study table) so `notedb.collect`, which refuses to
read past the seal before fetching anything, does not import `cli` and create a cycle: `cli`
already imports from `notedb`.
"""

from __future__ import annotations

TEST_WINDOW_START = "2025-11-01"
