"""Value objects of the mammogram assessment domain. Pure Python, no external dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import InvalidProbabilityError, InvalidThresholdsError

_SUM_TOLERANCE = 0.02


@dataclass(frozen=True)
class Probability:
    value: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise InvalidProbabilityError(f"Probability must be in [0, 1], got {self.value}")

    def __float__(self) -> float:
        return self.value


class BiradsGroup(Enum):
    """BI-RADS categories grouped by clinical action."""

    ROUTINE = "BI-RADS 1-2"
    SHORT_FOLLOW_UP = "BI-RADS 3"
    BIOPSY = "BI-RADS 4-5"


@dataclass(frozen=True)
class BiradsProbabilities:
    routine: Probability
    short_follow_up: Probability
    biopsy: Probability

    def __post_init__(self) -> None:
        total = self.routine.value + self.short_follow_up.value + self.biopsy.value
        if abs(total - 1.0) > _SUM_TOLERANCE:
            raise InvalidProbabilityError(f"BI-RADS probabilities must add up to 1, got {total:.3f}")

    @classmethod
    def of(cls, routine: float, short_follow_up: float, biopsy: float) -> BiradsProbabilities:
        return cls(Probability(routine), Probability(short_follow_up), Probability(biopsy))

    @classmethod
    def mean(cls, items: tuple[BiradsProbabilities, ...]) -> BiradsProbabilities:
        n = len(items)
        return cls.of(sum(i.routine.value for i in items) / n,
                      sum(i.short_follow_up.value for i in items) / n,
                      sum(i.biopsy.value for i in items) / n)

    def most_likely(self) -> BiradsGroup:
        ranked = sorted([(self.routine.value, BiradsGroup.ROUTINE),
                         (self.short_follow_up.value, BiradsGroup.SHORT_FOLLOW_UP),
                         (self.biopsy.value, BiradsGroup.BIOPSY)], key=lambda t: t[0], reverse=True)
        return ranked[0][1]


@dataclass(frozen=True)
class SuspicionScores:
    """suspicion = P(BI-RADS 4-5); attention = P(BI-RADS 3-5), i.e. 1 - P(BI-RADS 1-2)."""

    suspicion: Probability
    attention: Probability

    @classmethod
    def of(cls, suspicion: float, attention: float) -> SuspicionScores:
        return cls(Probability(suspicion), Probability(attention))


class ScoringMode(Enum):
    """FUSED combines the global and the high-resolution patch models; GLOBAL_ONLY is used when
    the image does not have enough resolution for the patch model."""

    FUSED = "fused"
    GLOBAL_ONLY = "global_only"


@dataclass(frozen=True)
class TriageThresholds:
    high_suspicion: Probability
    review_attention: Probability

    @classmethod
    def of(cls, high_suspicion: float, review_attention: float) -> TriageThresholds:
        return cls(Probability(high_suspicion), Probability(review_attention))

    def __post_init__(self) -> None:
        if self.high_suspicion.value <= 0 or self.review_attention.value <= 0:
            raise InvalidThresholdsError("Thresholds must be greater than zero")


class TriageLevel(Enum):
    HIGH_PRIORITY = "high_priority"
    REVIEW = "review"
    NO_SUSPICIOUS_FINDINGS = "no_suspicious_findings"

    @property
    def severity(self) -> int:
        return {"high_priority": 2, "review": 1, "no_suspicious_findings": 0}[self.value]


class BreastSide(Enum):
    RIGHT = "R"
    LEFT = "L"
    UNKNOWN = "unknown"


class ViewType(Enum):
    CC = "CC"
    MLO = "MLO"
    UNKNOWN = "unknown"


class QualityIssue(Enum):
    LOW_RESOLUTION = "low_resolution"
    BLURRY = "blurry"
    GLARE = "glare"
    SMALL_BREAST = "small_breast"
