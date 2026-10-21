"""Input/output DTOs of the analysis use cases (the domain is never exposed directly)."""
from __future__ import annotations

from dataclasses import dataclass, field

from mammo_application.abstractions import EncodedImage


@dataclass(frozen=True)
class AnalyzeMammogramCommand:
    image: EncodedImage
    panel_count: int | None = None          # None = detect separators automatically
    sides: tuple[str, ...] | None = None     # per panel: "R", "L" or "unknown"
    views: tuple[str, ...] | None = None     # per panel: "CC", "MLO" or "unknown"
    include_heatmap: bool = True


@dataclass(frozen=True)
class ImageQuery:
    image: EncodedImage
    panel_count: int | None = None


@dataclass(frozen=True)
class ViewResultDto:
    panel: int
    side: str
    view: str
    probabilities: dict[str, float]
    quality_issues: list[str]
    heatmap: EncodedImage | None
    heatmap_unavailable_reason: str | None
    # Most suspicious windows of the patch model; boxes normalized to the heatmap image. Empty without patches.
    regions: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class BreastResultDto:
    label: str
    side: str
    level: str
    scoring_mode: str
    suspicion: float
    attention: float
    most_likely: str
    probabilities: dict[str, float]
    quality_issues: list[str]
    views: list[ViewResultDto]


@dataclass(frozen=True)
class RejectedPanelDto:
    panel: int
    mammogram_confidence: float


@dataclass(frozen=True)
class AnalysisResultDto:
    overall_level: str
    breasts: list[BreastResultDto]
    rejected_panels: list[RejectedPanelDto]
    masked_text_regions: int
    model_version: str


@dataclass(frozen=True)
class PanelCheckDto:
    panel: int
    is_mammogram: bool
    mammogram_confidence: float
    quality_issues: list[str]


@dataclass(frozen=True)
class MammogramCheckDto:
    panels: list[PanelCheckDto]
    model_version: str


@dataclass(frozen=True)
class MaskedImageDto:
    image: EncodedImage
    masked_regions: int
    region_categories: dict[str, int] = field(default_factory=dict)
