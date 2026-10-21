"""PyTorch backend: timm networks (CPU or CUDA) and EasyOCR. The only module that imports torch, timm or easyocr.

Used with bundles in the "torch" format (the training checkpoints). Install with: pip install ".[torch]".
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import timm
import torch
import torch.nn.functional as F

from ..bundle import NETWORK_CLASSES, ModelBundle
from .constants import GATE_MEAN, GATE_STD
from .inference import Backend, LoadedNetwork
from .text_masking import Detection

PATCH_BATCH_SIZE = 96


def resolve_device(name: str) -> torch.device:
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(name)


class TorchNetwork:
    def __init__(self, net: torch.nn.Module, device: torch.device, mean: float, std: float,
                 mixed_precision: bool) -> None:
        self._net, self._device, self._mean, self._std = net, device, mean, std
        # float16 on CUDA for the classifiers, as in training; the gate always runs in float32.
        self._autocast = mixed_precision and device.type == "cuda"

    @torch.no_grad()
    def probabilities(self, batch: np.ndarray) -> np.ndarray:
        x = torch.from_numpy(np.ascontiguousarray(batch)).unsqueeze(1).to(self._device)
        x = (x.float() / 255 - self._mean) / self._std
        with torch.autocast(self._device.type, enabled=self._autocast):
            logits = self._net(x)
        return F.softmax(logits.float(), 1).cpu().numpy()


def load_network(path: Path, name: str, device: torch.device) -> LoadedNetwork:
    # weights_only=True: checkpoints hold tensors and plain metadata, so no pickled code is ever executed.
    ckpt = torch.load(path, map_location=device, weights_only=True)
    net = timm.create_model(ckpt["arch"], pretrained=False, num_classes=NETWORK_CLASSES[name], in_chans=1)
    net.load_state_dict(ckpt["state_dict"])
    net.to(device).eval()
    meta = {k: v for k, v in ckpt.items() if k != "state_dict"}
    is_gate = name == "gate"
    mean, std = (GATE_MEAN, GATE_STD) if is_gate else (float(meta["mean"]), float(meta["std"]))
    return LoadedNetwork(TorchNetwork(net, device, mean, std, mixed_precision=not is_gate), meta)


class EasyOcrDetector:
    box_margin = 1.0
    max_side = None

    def __init__(self, use_gpu: bool) -> None:
        import easyocr  # heavy import: only when the detector is created
        self._reader = easyocr.Reader(["es", "en"], gpu=use_gpu, verbose=False)

    def detect(self, rgb: np.ndarray) -> list[Detection]:
        return [(box, text, float(conf)) for box, text, conf in self._reader.readtext(rgb, paragraph=False)]


def load_backend(bundle: ModelBundle, device_name: str) -> Backend:
    device = resolve_device(device_name)
    networks = {name: load_network(bundle.network_path(name), name, device) for name in NETWORK_CLASSES}
    return Backend(networks, EasyOcrDetector(use_gpu=device.type == "cuda"), PATCH_BATCH_SIZE)
