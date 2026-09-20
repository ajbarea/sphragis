"""How large will the sealed test window be, and does the capture model predict the dev one?

The power analysis assumed 880 OpenStack changes and 2,400 Qt. That came from scaling the
dev window, which is the most censored window in the corpus. The train window is barely
censored, so it is the better base -- and the dev window then becomes a held-out check on
the capture model rather than an input to it.
"""

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

# How far the dev-window prediction may miss before the capture model is not usable for
# projecting the sealed window. The check exists to be able to fail; printing the miss and
# projecting anyway is what it was doing.
DEV_TOLERANCE = 0.15

ASSUMED = {"openstack": 880, "qt": 2400}

for org in ("openstack", "qt"):
    report_path = Path(f"datasets/results/window-report-{org}.json")
    report = json.loads(report_path.read_text())[org]["windows"]
    collected_through = last_collected(org)
    cdf = lynden_bell(
        observations(org, month_index(f"{collected_through}-01"), only=example_bearing(org))
    )

    train = window_capture(cdf, *WINDOWS["train"], collected_through)
    dev = window_capture(cdf, *WINDOWS["dev"], collected_through)
    test = window_capture(cdf, *WINDOWS["test"], "2027-02")

    train_months = len(train["months"])
    observed = report["train"]["changes"]
    true_rate = observed / train["mean_captured"] / train_months

    print(f"\n=== {org} ===")
    print(
        f"train: {observed} changes observed over {train_months} months, "
        f"captured {train['mean_captured']:.3f} -> {true_rate:.1f} true changes/month"
    )

    # Held-out check: predict the dev window from the train window's rate, and compare.
    dev_months = len(dev["months"])
    predicted = true_rate * dev_months * dev["mean_captured"]
    actual = report["dev"]["changes"]
    miss = abs(predicted - actual) / actual
    verdict = "within" if miss <= DEV_TOLERANCE else "OUTSIDE"
    print(
        f"dev predicted {predicted:.0f} changes, actual {actual} "
        f"({100 * (predicted - actual) / actual:+.1f}%, {verdict} "
        f"the {100 * DEV_TOLERANCE:.0f}% tolerance)"
    )

    if miss > DEV_TOLERANCE:
        print(
            "  refusing to project the sealed window: the capture model does not reproduce "
            "the one window held out from it, so a projection from it means nothing.\n"
            "  (the check also assumes a constant arrival rate; it cannot say which failed)"
        )
        continue
    test_months = len(test["months"])
    projected = true_rate * test_months * test["mean_captured"]
    print(
        f"test projected {projected:.0f} changes over {test_months} months "
        f"(captured {test['mean_captured']:.3f})"
    )
    print(f"power analysis assumed {ASSUMED[org]}: projection is {projected / ASSUMED[org]:.2f}x")
