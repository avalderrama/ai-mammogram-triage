"""Port implementations (internal: only the composition root creates them, via registration.py)."""
from __future__ import annotations

import math
import threading

import numpy as np

from mammo_application.abstractions import EncodedImage, GateVerdict, MaskedImage, PanelPrediction, SuspiciousRegion
from mammo_domain.assessment import (BiradsProbabilities, QualityIssue, ScoringMode, SuspicionScores,
                                     TriageThresholds)

from .bundle import ModelBundle
from .ml.classifiers import GlobalEnsemble, PatchScanner, render_heatmap, render_heatmap_layers
from .ml.gate_model import GateModel
from .ml.imaging import decode, encode_png, to_gray_array
from .ml.panels import equal_panels, find_panels
from .ml.regions import find_regions
from .ml.text_masking import TextMasker

_EPS = 1e-4


class _OcrPersonalDataMasker:
    def __init__(self, masker: TextMasker, lock: threading.Lock) -> None:
        self._masker, self._lock = masker, lock

    def mask(self, image: EncodedImage) -> MaskedImage:
        img = decode(image)
        with self._lock:
            masked, regions, cats = self._masker.mask(img)
        return MaskedImage(encode_png(masked), regions, cats)


class _PanelSplitter:
    def split(self, image: EncodedImage, panel_count: int | None) -> list[EncodedImage]:
        gray = to_gray_array(decode(image))
        bounds = equal_panels(gray.shape[1], panel_count) if panel_count else find_panels(gray)
        if len(bounds) == 1:
            return [encode_png(gray)]
        return [encode_png(np.ascontiguousarray(gray[:, x0:x1])) for x0, x1 in bounds]


class _MammogramGate:
    def __init__(self, model: GateModel, lock: threading.Lock) -> None:
        self._model, self._lock = model, lock

    def check(self, panel: EncodedImage) -> GateVerdict:
        img = decode(panel)
        with self._lock:
            p = self._model.p_mammogram(img)
        if p < self._model.threshold:
            return GateVerdict(False, p)
        issues = tuple(QualityIssue(q) for q in self._model.quality_problems(img))
        return GateVerdict(True, p, issues)


class _BiradsClassifier:
    def __init__(self, ensemble: GlobalEnsemble, scanner: PatchScanner, min_patch_height: int,
                 lock: threading.Lock) -> None:
        self._ensemble, self._scanner = ensemble, scanner
        self._min_patch_height, self._lock = min_patch_height, lock

    def predict(self, panel: EncodedImage, with_heatmap: bool, heatmap_layers: bool = False) -> PanelPrediction:
        gray = to_gray_array(decode(panel))
        with self._lock:
            p = self._ensemble.probabilities(gray)
            probabilities = BiradsProbabilities.of(*[float(v) for v in p / p.sum()])
            global_scores = SuspicionScores.of(float(p[2]), float(1 - p[0]))
            if gray.shape[0] < self._min_patch_height:
                return PanelPrediction(probabilities, global_scores, None, None,
                                       f"low_resolution: needs at least {self._min_patch_height} px of height")
            p45, lesion, heat, breast = self._scanner.scan(gray)
        heatmap = overlay = None
        if with_heatmap and heatmap_layers:
            heatmap, overlay = (encode_png(layer) for layer in render_heatmap_layers(breast, heat))
        elif with_heatmap:
            heatmap = encode_png(render_heatmap(breast, heat))
        regions = tuple(SuspiciousRegion(**r) for r in find_regions(heat, breast))
        return PanelPrediction(probabilities, global_scores, SuspicionScores.of(p45, lesion), heatmap, regions=regions,
                               heatmap_overlay=overlay)


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1 - _EPS)
    return math.log(p / (1 - p))


class _LogisticScoreFuser:
    """Fusion learned by calibrate_fusion.py: logistic regression on the logits of both models."""

    def __init__(self, calibration: dict) -> None:
        self._p45 = calibration["fused"]["p45"]
        self._att = calibration["fused"]["attention"]

    @staticmethod
    def _apply(params: dict, a: float, b: float) -> float:
        z = params["coef"][0] * _logit(a) + params["coef"][1] * _logit(b) + params["intercept"]
        return 1 / (1 + math.exp(-z))

    def fuse(self, global_scores: SuspicionScores, patch_scores: SuspicionScores) -> SuspicionScores:
        return SuspicionScores.of(
            self._apply(self._p45, global_scores.suspicion.value, patch_scores.suspicion.value),
            self._apply(self._att, global_scores.attention.value, patch_scores.attention.value))


class _BundleThresholds:
    def __init__(self, bundle: ModelBundle) -> None:
        c = bundle.calibration
        self._thresholds = {
            ScoringMode.FUSED: TriageThresholds.of(c["fused"]["thresholds"]["high_p45"],
                                                   c["fused"]["thresholds"]["review_attention"]),
            ScoringMode.GLOBAL_ONLY: TriageThresholds.of(c["global_only"]["thresholds"]["high_p45"],
                                                         c["global_only"]["thresholds"]["review_attention"]),
        }
        self._version = bundle.version

    def thresholds(self) -> dict[ScoringMode, TriageThresholds]:
        return dict(self._thresholds)

    def model_version(self) -> str:
        return self._version

