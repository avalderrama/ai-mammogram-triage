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
    # The red areas drawn over the processed breast; with heatmap layers, the processed breast alone and
    # heatmap_overlay holds the red areas as a transparent image of the same size.
    heatmap: EncodedImage | None
    patch_unavailable_reason: str | None = None
    regions: tuple[SuspiciousRegion, ...] = ()
    heatmap_overlay: EncodedImage | None = None


class BiradsClassifier(Protocol):
    def predict(self, panel: EncodedImage, with_heatmap: bool, heatmap_layers: bool = False) -> PanelPrediction: ...


class ScoreFuser(Protocol):
    def fuse(self, global_scores: SuspicionScores, patch_scores: SuspicionScores) -> SuspicionScores: ...


class ThresholdsProvider(Protocol):
    def thresholds(self) -> dict[ScoringMode, TriageThresholds]: ...

    def model_version(self) -> str: ...


@dataclass(frozen=True)
class UsageEvent:
    """One analysis, as the usage counter sees it: no image, no result details, no identity it keeps."""
    outcome: str  # the overall level, "not_a_mammogram" or "error"
    views: int = 0
    detailed_views: int = 0  # views the high-resolution model analyzed (heatmap available)
    # The verified identity of whoever asked, to count distinct people per day. The usage log keeps only a keyed hash
    # of the day and the identity, for the current day, and never stores the identity itself.
    requester: str | None = None


@dataclass(frozen=True)
class DailyUsage:
    day: str  # ISO date in the usage log's time zone
    analyses: int = 0
    high_priority: int = 0
    review: int = 0
    no_suspicious_findings: int = 0
    not_a_mammogram: int = 0
    errors: int = 0
    views: int = 0
    detailed_views: int = 0
    people: int = 0


class UsageLog(Protocol):
    def record(self, event: UsageEvent) -> None:
        """Counts one analysis. Must never raise: usage counting cannot break an analysis."""

    def daily(self, days: int) -> list[DailyUsage]:
        """The last `days` days with activity, most recent first."""
