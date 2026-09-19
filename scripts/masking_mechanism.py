"""Why does masking an update with noise make the attacker *better*?

The defence curve reports it at both pool sizes: at 200 rounds, AOSP is detected at AUC 0.992
with no mask and 0.998 with a mask of the mean update norm. A defence that helps the attacker is
either a bug or a mechanism, and this decides which.

The candidate mechanism is the cosine. The attacker scores

    cos(held - without, direction),

and a cosine is scale free, so noise cannot hurt it by shrinking the difference -- it hurts by
turning it. Adding an isotropic mask `n` to a difference `v` attenuates the cosine by about

    ||v|| / sqrt(||v||^2 + ||n||^2),

which depends on `v`'s own length. The two classes differ in exactly that length: a member round's
difference carries the target's update at 1/size, a non-member round's difference carries only
which outsiders were drawn. So the same mask deflates the null class *more* than the member class,
the null's spread collapses toward zero faster than the member's mean does, and their separation
can widen. The effect must grow with rounds, because averaging removes the sampling term from
`v` and leaves the classes' lengths further apart.

The prediction is quantitative, so it can be wrong: measure each class's difference norm without a
mask, attenuate each class's no-mask scores by its own predicted factor, and the AUC that comes out
should track the measured one. If it does not, the anomaly is not this.

    uv run --no-sync --no-active python scripts/masking_mechanism.py \
        --vectors datasets/results/client-vectors-cpp-early.npz \
        --clients datasets/results/client-updates-cpp-early.json \
        --out datasets/results/masking-mechanism.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from sphragis.measure.aggregate import tpr_at_fpr
from sphragis.measure.attribution import Source
from sphragis.provenance import provenance_header

sys.path.insert(0, str(Path(__file__).resolve().parent))
from defence_curve import auc, cosine  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--vectors", type=Path, required=True)
parser.add_argument("--clients", type=Path, required=True)
parser.add_argument("--organization", default="aosp")
parser.add_argument("--noise", type=float, nargs="+", default=[0.0, 1.0, 4.0, 16.0])
parser.add_argument("--rounds", type=int, nargs="+", default=[1, 10, 50, 200])
parser.add_argument("--round-size", type=int, default=8)
parser.add_argument("--draws", type=int, default=400)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--repeats", type=int, default=5, help="redraws per cell, for its spread")
parser.add_argument("--out", type=Path, required=True)


def differences(
    vectors: np.ndarray,
    *,
    members: np.ndarray,
    outside: np.ndarray,
    size: int,
    rounds: int,
    mask_sd: float,
    draws: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """The attacker's difference vector for each draw, before it is turned into a cosine.

    The difference of two averages of averages is a weighted sum of the clients' updates, so the
    participants are accumulated as a count per client and one matrix product does the rest.
    Gathering the rows instead costs `draws * rounds * size` reads of a 50,176-coordinate row and
    is what made the direct version untenable.
    """
    width = vectors.shape[1]
    weights = np.zeros((draws, len(vectors)))
    for draw in range(draws):
        for _ in range(rounds):
            if members is not None:
                participants = np.concatenate(
                    [rng.choice(members, 1), rng.choice(outside, size - 1, replace=False)]
                )
            else:
                participants = rng.choice(outside, size, replace=False)
            np.add.at(weights[draw], participants, 1.0 / (rounds * size))
            np.add.at(
                weights[draw], rng.choice(outside, size, replace=False), -1.0 / (rounds * size)
            )
    out = weights @ vectors
    if mask_sd:
        # `rounds` independent masks in each of the two averages: their difference is one
        # Gaussian of deviation mask_sd * sqrt(2 / rounds), drawn directly.
        out = out + rng.normal(0.0, mask_sd * np.sqrt(2.0 / rounds), (draws, width))
    return out


def main() -> None:
    args = parser.parse_args()
    loaded = np.load(args.vectors, allow_pickle=False)
    vectors = loaded["vectors"]
    names = [str(n).split("/", 1)[1] for n in loaded["names"]]
    clients = json.loads(args.clients.read_text())["clients"]
    sources = [Source.parse(clients[n]["source"]) for n in names]
    mine = np.array([i for i, s in enumerate(sources) if s.organization == args.organization])
    outside = np.array([i for i, s in enumerate(sources) if s.organization != args.organization])
    half = max(1, len(mine) // 2)
    reference_ids, participants = mine[:half], mine[half:]
    direction = vectors[reference_ids].mean(axis=0)
    scale = float(np.linalg.norm(vectors, axis=1).mean())
    width = vectors.shape[1]

    report: dict = {
        "organization": args.organization,
        "round_size": args.round_size,
        "draws": args.draws,
        "mean_update_norm": scale,
        "provenance": provenance_header(),
        "cells": {},
    }
    for rounds in args.rounds:
        for noise in args.noise:
            # The mask left on the difference after averaging: `rounds` masks in each of the two
            # averages, each already the mean of `size` clients' independent masks.
            mask_sd = noise * scale / np.sqrt(width * args.round_size)
            residual = mask_sd * np.sqrt(2.0 / rounds) * np.sqrt(width)
            sigma = residual / np.sqrt(width)
            measured_aucs, predicted_aucs, unmasked_aucs, tprs = [], [], [], []
            projection_aucs: list[float] = []
            achieved: list[float] = []
            attenuations = {"present": [], "absent": []}
            norms = {"present": [], "absent": []}
            for repeat in range(args.repeats):
                # Each repeat redraws everything, so the spread reported is the spread an AUC
                # over `draws` rounds actually has. Reading one draw of it called a 0.05 swing a
                # result on the first pass of this script.
                stream = args.seed + 7919 * rounds + 104729 * repeat
                base_rng = np.random.default_rng(stream)
                base_vectors = {
                    "present": differences(
                        vectors,
                        members=participants,
                        outside=outside,
                        size=args.round_size,
                        rounds=rounds,
                        mask_sd=0.0,
                        draws=args.draws,
                        rng=base_rng,
                    ),
                    "absent": differences(
                        vectors,
                        members=None,
                        outside=outside,
                        size=args.round_size,
                        rounds=rounds,
                        mask_sd=0.0,
                        draws=args.draws,
                        rng=base_rng,
                    ),
                }
                unit = direction / np.linalg.norm(direction)
                base, lengths, predicted = {}, {}, {}
                noise_rng = np.random.default_rng(stream + 31 + int(noise * 1000))
                for side, drawn in base_vectors.items():
                    lengths[side] = np.linalg.norm(drawn, axis=1)
                    base[side] = (drawn @ unit) / np.where(lengths[side] > 0, lengths[side], 1.0)
                    # cos(v + n, d) = (v.d + n.d) / ||v + n||, per draw rather than per class:
                    # a draw whose difference is nearly zero can score any cosine at all, and it
                    # is exactly that draw the mask rescales away. A class mean cannot see it.
                    projected = drawn @ unit + noise_rng.normal(0.0, sigma, args.draws)
                    predicted[side] = projected / np.sqrt(lengths[side] ** 2 + residual**2)
                    attenuations[side].append(
                        float(np.mean(lengths[side] / np.sqrt(lengths[side] ** 2 + residual**2)))
                    )
                    norms[side].append(float(lengths[side].mean()))
                measured_rng = np.random.default_rng(stream + 13 * int(noise * 4) + 1)
                got = {
                    side: differences(
                        vectors,
                        members=participants if side == "present" else None,
                        outside=outside,
                        size=args.round_size,
                        rounds=rounds,
                        mask_sd=mask_sd,
                        draws=args.draws,
                        rng=measured_rng,
                    )
                    for side in ("present", "absent")
                }
                measured = {side: [cosine(v, direction) for v in got[side]] for side in got}
                # The same draws read by a statistic that is not scale free. If the mask is a
                # real defence and only the cosine misreads it, this one falls monotonically.
                projected = {side: list(got[side] @ unit) for side in got}
                projection_aucs.append(auc(projected["present"], projected["absent"]))
                measured_aucs.append(auc(measured["present"], measured["absent"]))
                predicted_aucs.append(auc(list(predicted["present"]), list(predicted["absent"])))
                unmasked_aucs.append(auc(list(base["present"]), list(base["absent"])))
                point = tpr_at_fpr(measured["present"], measured["absent"], 0.01)
                tprs.append(point["tpr"])
                achieved.append(point["fpr_achieved"])
            cell = {
                "auc_measured": float(np.mean(measured_aucs)),
                "auc_measured_sd": float(np.std(measured_aucs, ddof=1))
                if args.repeats > 1
                else 0.0,
                "auc_predicted": float(np.mean(predicted_aucs)),
                "auc_unmasked": float(np.mean(unmasked_aucs)),
                "auc_unmasked_sd": float(np.std(unmasked_aucs, ddof=1))
                if args.repeats > 1
                else 0.0,
                "repeats": args.repeats,
                "tpr_at_1pct_fpr": float(np.mean(tprs)),
                "fpr_achieved_at_1pct": float(np.mean(achieved)),
                "auc_projection": float(np.mean(projection_aucs)),
                "auc_projection_sd": (
                    float(np.std(projection_aucs, ddof=1)) if args.repeats > 1 else 0.0
                ),
                "difference_norm_present": float(np.mean(norms["present"])),
                "difference_norm_absent": float(np.mean(norms["absent"])),
                "residual_mask_norm": residual,
                "attenuation_present": float(np.mean(attenuations["present"])),
                "attenuation_absent": float(np.mean(attenuations["absent"])),
            }
            report["cells"][f"{rounds}/{noise}"] = cell
            print(
                f"{rounds:4} rounds, noise {noise:5}: AUC {cell['auc_measured']:.3f} "
                f"(sd {cell['auc_measured_sd']:.3f}) measured, {cell['auc_predicted']:.3f} "
                f"predicted, {cell['auc_unmasked']:.3f} (sd {cell['auc_unmasked_sd']:.3f}) "
                f"unmasked  (attenuation {cell['attenuation_present']:.3f} member, "
                f"{cell['attenuation_absent']:.3f} null), projection AUC "
                f"{cell['auc_projection']:.3f} (sd {cell['auc_projection_sd']:.3f})",
                flush=True,
            )
            args.out.write_text(json.dumps(report, indent=2))
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
