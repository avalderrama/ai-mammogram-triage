"""ONNX Runtime backend (CPU): the networks exported by the training repository's export_onnx.py and RapidOCR.
The only module that imports onnxruntime or rapidocr_onnxruntime.

Same results as PyTorch in float32 on CPU with ~8x less memory (no PyTorch, timm or EasyOCR in the process).
Install with: pip install ".[onnx]".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort

from ..bundle import NETWORK_CLASSES, BundleError, ModelBundle
from .constants import GATE_MEAN, GATE_STD
from .inference import Backend, LoadedNetwork, normalize, softmax
from .text_masking import Detection

# Small batches keep the peak memory low on CPU; the scores do not depend on the batch size.
PATCH_BATCH_SIZE = 16
OCR_MAX_SIDE = 1024
MIN_TEXT_CONFIDENCE = 0.3
REQUIRED_META = {"gate": {"umbral", "calidad"}, "global_512": {"height", "mean", "std"},
                 "global_1024": {"height", "mean", "std"}, "patch_2048": {"mean", "std"}}


class OnnxNetwork:
    def __init__(self, path: Path, mean: float, std: float, threads: int = 0) -> None:
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = threads, 1
        # Without the arena, buffers are released after every run: low resident memory between requests.
        options.enable_cpu_mem_arena = False
        self._session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name
        self._mean, self._std = mean, std

    def probabilities(self, batch: np.ndarray) -> np.ndarray:
        return softmax(self._session.run(None, {self._input: normalize(batch, self._mean, self._std)})[0])


def load_network(path: Path, name: str, meta: dict[str, Any]) -> LoadedNetwork:
    mean, std = (GATE_MEAN, GATE_STD) if name == "gate" else (float(meta["mean"]), float(meta["std"]))
    return LoadedNetwork(OnnxNetwork(path, mean, std), meta)


class RapidOcrDetector:
    """RapidOCR (PP-OCR detection and recognition on ONNX Runtime)."""

    # PP-OCR expands each box (unclip ratio 1.6): its boxes are ~10% taller than EasyOCR's for the same text.
    box_margin = 1.15
    # TextMasker reads larger images in tiles of this size, at full resolution (bounded peak memory, small print kept).
    max_side = OCR_MAX_SIDE

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR
        # text_score: RapidOCR drops results under 0.5 by default; the masking rule keeps them from 0.3.
        # det_limit_type "max": by default the detector ENLARGES images until the short side is 736 px (a 2720x453
        # figure became ~4400 px wide and the peak memory doubled). With "max", RapidOCR 1.4 only shrinks inputs
        # larger than 2000 px (it ignores det_limit_side_len), so the <= 1024 px tiles are read as they are.
        self._engine = RapidOCR(text_score=MIN_TEXT_CONFIDENCE, det_limit_type="max")

    def detect(self, rgb: np.ndarray) -> list[Detection]:
        # RapidOCR treats arrays as OpenCV images (BGR); only paths, bytes and PIL images are converted for you.
        result, _ = self._engine(np.ascontiguousarray(rgb[..., ::-1]))
        return [(box, text, float(conf)) for box, text, conf in result or []]


def load_backend(bundle: ModelBundle) -> Backend:
    metas = json.loads(bundle.path("onnx_meta.json").read_text(encoding="utf-8"))
    incomplete = [n for n, keys in REQUIRED_META.items() if not keys <= set(metas.get(n, {}))]
    if incomplete:
        raise BundleError(f"onnx_meta.json at {bundle.root} lacks the metadata of: {', '.join(incomplete)} "
                          "(export the bundle again with export_onnx.py)")
    networks = {name: load_network(bundle.network_path(name), name, metas[name]) for name in NETWORK_CLASSES}
    return Backend(networks, RapidOcrDetector(), PATCH_BATCH_SIZE)
