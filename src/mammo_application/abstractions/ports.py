"""Ports (outbound abstractions) implemented by the infrastructure ring."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from mammo_domain.assessment import (BiradsProbabilities, QualityIssue, ScoringMode, SuspicionScores,
                                     TriageThresholds)

from .images import EncodedImage


@dataclass(frozen=True)
class MaskedImage:
    image: EncodedImage
    masked_regions: int
    region_categories: dict[str, int] = field(default_factory=dict)


class PersonalDataMasker(Protocol):
    def mask(self, image: EncodedImage) -> MaskedImage:
        """Cover every text region drawn over the image. Never returns or logs the text read."""


class ImageDecodingError(Exception):
    """The bytes are not a readable image."""


class ImageTooManyPixelsError(ImageDecodingError):
    """The image is readable but exceeds the pixel budget (decompression-bomb defence)."""


class PanelSplitter(Protocol):
    def split(self, image: EncodedImage, panel_count: int | None) -> list[EncodedImage]:
        """Split side-by-side views. panel_count=None detects separators automatically."""


@dataclass(frozen=True)
class GateVerdict:
    is_mammogram: bool
    confidence: float
    quality_issues: tuple[QualityIssue, ...] = ()


class MammogramGate(Protocol):
    def check(self, panel: EncodedImage) -> GateVerdict: ...


@dataclass(frozen=True)
class SuspiciousRegion:
    """A window the patch model found most suspicious. Box normalized (0-1) to the heatmap image."""
    x: float
    y: float
    width: float
    height: float
    score: float
    depth: str      # "posterior" (near the chest wall) | "middle" | "anterior" (near the nipple)
    vertical: str   # "upper" | "middle" | "lower" third of the breast in the image


@dataclass(frozen=True)
class PanelPrediction:
    probabilities: BiradsProbabilities
    global_scores: SuspicionScores
    patch_scores: SuspicionScores | None
    heatmap: EncodedImage | None
    patch_unavailable_reason: str | None = None
    regions: tuple[SuspiciousRegion, ...] = ()


class BiradsClassifier(Protocol):
    def predict(self, panel: EncodedImage, with_heatmap: bool) -> PanelPrediction: ...


class ScoreFuser(Protocol):
    def fuse(self, global_scores: SuspicionScores, patch_scores: SuspicionScores) -> SuspicionScores: ...


class ThresholdsProvider(Protocol):
    def thresholds(self) -> dict[ScoringMode, TriageThresholds]: ...

    def model_version(self) -> str: ...
