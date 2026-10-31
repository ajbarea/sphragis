"""Guards in `scripts/rq1_pilot.py` that its CPU tests cannot reach, since it imports torch."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_foreign_arms_are_added_only_to_a_stored_run() -> None:
    """Without --reuse-from the halves would be trained again over the adapters H1 read."""
    pilot = (ROOT / "scripts" / "rq1_pilot.py").read_text()
    guard = pilot.index('raise SystemExit("foreign arms are added to a stored run')
    assert pilot.rfind("if not args.reuse_from:", 0, guard) > pilot.index("if args.foreign_pool:")
    assert guard < pilot.index("matched_foreign_arms(")
    job = (ROOT / "scripts" / "foreign_arms.sbatch").read_text()
    assert '--reuse-from "$STORED"' in job and "--foreign-pool" in job


def test_foreign_arms_train_only_on_the_registered_source_and_pool() -> None:
    """Both checks run before any arm is drawn from the pool."""
    pilot = (ROOT / "scripts" / "rq1_pilot.py").read_text()
    draw = pilot.index("matched_foreign_arms(\n")
    assert pilot.index("registered_pool(args.foreign_source, exploratory=args.exploratory)") < draw
    assert pilot.index("if pool_digest(pool) != registered:") < draw
    job = (ROOT / "scripts" / "foreign_arms.sbatch").read_text()
    assert "1) EXPLORATORY_ARGS=(--exploratory) ;;" in job and '"${EXPLORATORY_ARGS[@]}"' in job
