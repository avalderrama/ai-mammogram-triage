"""Domain services of the assessment context."""
from __future__ import annotations

from .value_objects import SuspicionScores, TriageLevel, TriageThresholds


class TriagePolicy:
    """Three-level triage, chosen to minimize false alarms without losing suspicious cases:

    - HIGH_PRIORITY when P(BI-RADS 4-5) reaches the high-suspicion threshold
      (calibrated for ~5% false alarms).
    - REVIEW when the probability of needing attention (BI-RADS 3-5) reaches the review
      threshold (calibrated to catch ~85% of BI-RADS 4-5 together with HIGH_PRIORITY).
    - NO_SUSPICIOUS_FINDINGS otherwise. It never replaces the radiologist's reading.
    """

    @staticmethod
    def level_for(scores: SuspicionScores, thresholds: TriageThresholds) -> TriageLevel:
        if scores.suspicion.value >= thresholds.high_suspicion.value:
            return TriageLevel.HIGH_PRIORITY
        if scores.attention.value >= thresholds.review_attention.value:
            return TriageLevel.REVIEW
        return TriageLevel.NO_SUSPICIOUS_FINDINGS
