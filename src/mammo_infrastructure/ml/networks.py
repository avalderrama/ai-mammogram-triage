"""Network construction and checkpoint loading."""
from __future__ import annotations

from pathlib import Path

import timm
import torch


def resolve_device(name: str) -> torch.device:
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(name)


def load_network(path: Path, num_classes: int, device: torch.device) -> tuple[torch.nn.Module, dict]:
    # weights_only=True: checkpoints hold tensors and plain metadata, so no pickled code is ever executed.
    ckpt = torch.load(path, map_location=device, weights_only=True)
    net = timm.create_model(ckpt["arch"], pretrained=False, num_classes=num_classes, in_chans=1)
    net.load_state_dict(ckpt["state_dict"])
    net.to(device).eval()
    meta = {k: v for k, v in ckpt.items() if k != "state_dict"}
    return net, meta


def normalize(batch_uint8: torch.Tensor, mean: float, std: float) -> torch.Tensor:
    return (batch_uint8.float() / 255 - mean) / std
