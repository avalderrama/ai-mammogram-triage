"""Entities and aggregates of a mammogram study assessment."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .errors import InvalidBreastAssessmentError, NoMammogramFoundError
from .policies import TriagePolicy
from .value_objects import (BiradsProbabilities, BreastSide, QualityIssue, ScoringMode, SuspicionScores,
                            TriageLevel, TriageThresholds, ViewType)

ScoreFusion = Callable[[SuspicionScores, SuspicionScores], SuspicionScores]


@dataclass(frozen=True)
class ViewFinding:
    """Model output for one accepted mammogram panel (one view of one breast)."""

    panel_index: int
    side: BreastSide
    view: ViewType
    probabilities: BiradsProbabilities
    global_scores: SuspicionScores
    patch_scores: SuspicionScores | None
    quality_issues: tuple[QualityIssue, ...] = ()


@dataclass(frozen=True)
class BreastTriage:
    level: TriageLevel
    mode: ScoringMode
    scores: SuspicionScores


class BreastAssessment:
    """All views of one breast. A breast is triaged as a whole (views are combined)."""

    def __init__(self, side: BreastSide, findings: tuple[ViewFinding, ...], label: str) -> None:
        if not findings:
            raise InvalidBreastAssessmentError("A breast assessment needs at least one view")
        if any(f.side != side for f in findings):
            raise InvalidBreastAssessmentError("All views of a breast assessment must share the same side")
        self._side = side
        self._findings = findings
        self._label = label

    @property
    def side(self) -> BreastSide:
        return self._side

    @property
    def label(self) -> str:
        return self._label

    @property
    def findings(self) -> tuple[ViewFinding, ...]:
        return self._findings

    def probabilities(self) -> BiradsProbabilities:
        return BiradsProbabilities.mean(tuple(f.probabilities for f in self._findings))

    def global_scores(self) -> SuspicionScores:
        """Global model: average of the views (as calibrated)."""
        n = len(self._findings)
        return SuspicionScores.of(sum(f.global_scores.suspicion.value for f in self._findings) / n,
                                  sum(f.global_scores.attention.value for f in self._findings) / n)

    def patch_scores(self) -> SuspicionScores | None:
        """Patch model: most suspicious view (a lesion may be visible in only one view).
        Only available when every view had enough resolution for the patch model."""
        if any(f.patch_scores is None for f in self._findings):
            return None
        return SuspicionScores.of(max(f.patch_scores.suspicion.value for f in self._findings),  # type: ignore[union-attr]
                                  max(f.patch_scores.attention.value for f in self._findings))  # type: ignore[union-attr]

    def triage(self, fuse: ScoreFusion, thresholds: Mapping[ScoringMode, TriageThresholds]) -> BreastTriage:
        patch = self.patch_scores()
        if patch is None:
            mode, scores = ScoringMode.GLOBAL_ONLY, self.global_scores()
        else:
            mode, scores = ScoringMode.FUSED, fuse(self.global_scores(), patch)
        return BreastTriage(TriagePolicy.level_for(scores, thresholds[mode]), mode, scores)

    def quality_issues(self) -> tuple[QualityIssue, ...]:
        seen: list[QualityIssue] = []
        for f in self._findings:
            seen.extend(q for q in f.quality_issues if q not in seen)
        return tuple(seen)


class StudyAssessment:
    """Aggregate root: every breast found in the submitted image plus the rejected panels."""

    def __init__(self, breasts: tuple[BreastAssessment, ...], rejected_panels: tuple[int, ...],
                 masked_text_regions: int) -> None:
        self._breasts = breasts
        self._rejected_panels = rejected_panels
        self._masked_text_regions = masked_text_regions

    @classmethod
    def from_findings(cls, findings: tuple[ViewFinding, ...], rejected_panels: tuple[int, ...],
                      masked_text_regions: int) -> StudyAssessment:
        """Groups views by breast side. Panels with an unknown side are never combined with others."""
        if not findings:
            raise NoMammogramFoundError("No panel of the image was recognized as a mammogram")
        breasts: list[BreastAssessment] = []
        for side, label in [(BreastSide.RIGHT, "right"), (BreastSide.LEFT, "left")]:
            views = tuple(f for f in findings if f.side == side)
            if views:
                breasts.append(BreastAssessment(side, views, label))
        for f in findings:
            if f.side == BreastSide.UNKNOWN:
                breasts.append(BreastAssessment(BreastSide.UNKNOWN, (f,), f"panel {f.panel_index + 1}"))
        return cls(tuple(breasts), rejected_panels, masked_text_regions)

    @property
    def breasts(self) -> tuple[BreastAssessment, ...]:
        return self._breasts

    @property
    def rejected_panels(self) -> tuple[int, ...]:
        return self._rejected_panels

    @property
    def masked_text_regions(self) -> int:
        return self._masked_text_regions

    def triage(self, fuse: ScoreFusion,
               thresholds: Mapping[ScoringMode, TriageThresholds]) -> tuple[BreastTriage, ...]:
        return tuple(b.triage(fuse, thresholds) for b in self._breasts)

    @staticmethod
    def overall_level(triages: tuple[BreastTriage, ...]) -> TriageLevel:
        return max((t.level for t in triages), key=lambda lv: lv.severity)
