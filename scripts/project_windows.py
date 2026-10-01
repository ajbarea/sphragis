"""How large will the sealed test window be, and does the capture model predict the dev one?

The train window is barely censored, so its true arrival rate (changes observed over the months,
divided by the share the capture model says were captured) is the base; the dev window is the
held-out check on that model, and the sealed window is projected only if the check holds. The
test window is projected over its registered months (`WINDOWS`, twelve) at the earliest fetch the
registered horizon permits, on the corpus version the study runs: `--root` and `--window-report`
name it.

    uv run --no-sync --no-active python scripts/project_windows.py \\
        --root ../wm-bots/datasets/gerrit --org openstack \\
        --window-report datasets/results/window-report-openstack-v3.json \\
        --out datasets/results/project-windows-openstack-v3.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from censoring import (  # noqa: E402
    WINDOWS,
    example_bearing,
    last_collected,
    lynden_bell,
    month_index,
    observations,
    window_capture,
)

from sphragis.provenance import provenance_header  # noqa: E402

# How far the dev-window prediction may miss before the capture model is not usable for
# projecting the sealed window. The check exists to be able to fail. Set on 2026-09-16 after the
# repaired model's first check read +11.6% (research log), so it is a post hoc choice, recorded
# as one. Only an over-prediction past it refuses the projection: an under-prediction (more dev
# changes than the train rate predicts, as Wikimedia's rising arrivals give) makes the train-rate
# projection a lower bound on the sealed window, and a smaller planned window only enlarges the
# simulated bounds (research log, 2026-10-01). The projection is then marked as a lower bound.
DEV_TOLERANCE = 0.15
# Registered decisions, "Fetch horizon": no earlier than three months after the test window's
# last month. The projection reads the earliest permitted fetch, the most censored, so it does
# not count changes a permitted fetch could still miss.
HORIZON_MONTHS = 3


def earliest_fetch(last: str, months: int = HORIZON_MONTHS) -> str:
    """The month `months` after `last`, both as YYYY-MM."""
    index = month_index(f"{last}-01") + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


FETCH = earliest_fetch(WINDOWS["test"][1])


def dev_reading(miss: float, tolerance: float = DEV_TOLERANCE) -> str:
    """What a dev-window miss allows: `refuse`, `lower_bound`, or `holds`.

    `miss` is (predicted - actual) / actual, so a positive miss over-predicts the window.
    """
    if miss > tolerance:
        return "refuse"
    return "lower_bound" if miss < -tolerance else "holds"


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True, help="the corpus root the study runs on")
parser.add_argument("--org", required=True)
parser.add_argument("--window-report", type=Path, required=True, help="window_report.py's artifact")
parser.add_argument("--out", type=Path, required=True)


def main() -> None:
    args = parser.parse_args()
    report = json.loads(args.window_report.read_text())[args.org]["windows"]
    collected_through = last_collected(args.org, args.root)
    cdf = lynden_bell(
        observations(
            args.org,
            month_index(f"{collected_through}-01"),
            only=example_bearing(args.org, args.root),
            root=args.root,
        )
    )
    train = window_capture(cdf, *WINDOWS["train"], collected_through)
    dev = window_capture(cdf, *WINDOWS["dev"], collected_through)
    test = window_capture(cdf, *WINDOWS["test"], FETCH)

    train_months = len(train["months"])
    observed = report["train"]["changes"]
    true_rate = observed / train["mean_captured"] / train_months
    dev_months = len(dev["months"])
    predicted = true_rate * dev_months * dev["mean_captured"]
    actual = report["dev"]["changes"]
    miss = (predicted - actual) / actual
    out = {
        "org": args.org,
        "root": str(args.root.resolve()),
        "window_report": str(args.window_report),
        "collected_through": collected_through,
        "train": {
            "months": train_months,
            "changes": observed,
            "captured": train["mean_captured"],
            "true_changes_per_month": true_rate,
        },
        "dev_check": {
            "predicted": predicted,
            "actual": actual,
            "miss": miss,
            "tolerance": DEV_TOLERANCE,
            "reading": dev_reading(miss),
        },
        "test": None,
        "provenance": provenance_header(),
    }
    print(
        f"{args.org}: train {observed} changes over {train_months} months, captured "
        f"{train['mean_captured']:.3f} -> {true_rate:.1f} a month; dev predicted {predicted:.0f}, "
        f"actual {actual} ({100 * miss:+.1f}%)"
    )
    reading = out["dev_check"]["reading"]
    if reading == "refuse":
        args.out.write_text(json.dumps(out, indent=2) + "\n")
        raise SystemExit(
            f"refusing to project the sealed window: the dev over-prediction {100 * miss:+.1f}% "
            f"exceeds {100 * DEV_TOLERANCE:.0f}%, so the projection could overstate the window"
        )
    test_months = len(test["months"])
    out["test"] = {
        "months": test_months,
        "fetch": FETCH,
        "captured": test["mean_captured"],
        "projected_changes": true_rate * test_months * test["mean_captured"],
    }
    if reading == "lower_bound":
        out["test"]["lower_bound"] = True
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(
        f"test projected {out['test']['projected_changes']:.0f} changes over {test_months} months "
        f"(captured {test['mean_captured']:.3f} at {FETCH}); wrote {args.out}"
    )


if __name__ == "__main__":
    main()
