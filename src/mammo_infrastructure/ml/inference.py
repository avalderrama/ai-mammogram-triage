"""Contract between the model logic (numpy) and the inference backends (PyTorch or ONNX Runtime)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from .text_masking import TextDetector


class Network(Protocol):
    def probabilities(self, batch: np.ndarray) -> np.ndarray:
        """uint8 images (N, H, W) -> class probabilities (N, classes). Normalization is the network's job."""
        ...


@dataclass(frozen=True)
class LoadedNetwork:
    network: Network
    meta: dict[str, Any]  # checkpoint metadata: input height, mean/std, gate threshold and quality limits


@dataclass(frozen=True)
class Backend:
    """What a backend provides: the bundle's networks by name, the OCR engine and the patch batch size."""
    networks: dict[str, LoadedNetwork]
    text_detector: TextDetector
    patch_batch_size: int


def softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def normalize(batch: np.ndarray, mean: float, std: float) -> np.ndarray:
    """uint8 (N, H, W) -> float32 (N, 1, H, W), as in training."""
    return ((batch[:, None].astype(np.float32) / 255) - mean) / std
