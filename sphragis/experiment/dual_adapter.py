"""Shared plumbing for the adapter-instance cut: two adapters a client, one communicated.

FedDPA (`scripts/dual_adapter_updates.py`) and FDLoRA (`scripts/fdlora_schedule.py`) both split a
client's update into a global adapter that is communicated and a local/personalized one that is
not. What differs between them is the schedule that moves parameters between the two
(`sphragis.experiment.fdlora`); reading source specs, freezing one adapter while the other
trains, and writing an adapter where the geometry and attribution scripts expect to find it are
identical between the two, so they live once, here.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any

GLOBAL = "global"
LOCAL = "local"


def sources(args: argparse.Namespace) -> list[str]:
    """`--source` flags plus a `--sources-file`'s lines, comments and blanks stripped."""
    specs = list(args.source)
    if args.sources_file:
        for line in args.sources_file.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                specs.append(line)
    if not specs:
        raise SystemExit("no sources given")
    return specs


def freeze(model: Any, adapter: str) -> None:
    """Hold one adapter's parameters still while another trains against them.

    Only needed when both adapters are simultaneously active in the forward pass: an adapter that
    is merely inactive gets no gradient at all, and every built-in optimiser skips a parameter
    whose `.grad` is `None`, so freezing an adapter nobody is training is unneeded, not harmless
    caution.
    """
    for name, parameter in model.named_parameters():
        if "lora_" in name and f".{adapter}." in name:
            parameter.requires_grad_(False)


def unfreeze(model: Any, adapter: str) -> None:
    """Undo `freeze`, for a schedule that alternates which adapter trains."""
    for name, parameter in model.named_parameters():
        if "lora_" in name and f".{adapter}." in name:
            parameter.requires_grad_(True)


def save_adapter(model: Any, adapter: str, destination: Path) -> None:
    """Write one adapter where the geometry and attack scripts expect to find it.

    `save_pretrained` with a selection writes `<destination>/<adapter>/adapter_model.safetensors`,
    and every reader in this repository globs `<client>/adapter_model.safetensors`, so the files
    are lifted one level rather than teaching each reader a second layout.
    """
    destination.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(destination, selected_adapters=[adapter])
    nested = destination / adapter
    for path in nested.iterdir():
        shutil.move(str(path), destination / path.name)
    nested.rmdir()
