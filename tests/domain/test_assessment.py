import pytest

from mammo_domain.assessment import (BiradsGroup, BiradsProbabilities, BreastAssessment, BreastSide,
                                     InvalidBreastAssessmentError, InvalidProbabilityError, NoMammogramFoundError,
                                     QualityIssue, ScoringMode, StudyAssessment, SuspicionScores, TriageLevel,
                                     TriagePolicy, TriageThresholds, ViewFinding, ViewType)

THRESHOLDS = {ScoringMode.FUSED: TriageThresholds.of(0.08, 0.08),
              ScoringMode.GLOBAL_ONLY: TriageThresholds.of(0.20, 0.25)}


def fuse_take_patch(global_scores, patch_scores):
    return patch_scores


def finding(i, side=BreastSide.RIGHT, view=ViewType.CC, g=(0.05, 0.10), p=None, probs=(0.8, 0.15, 0.05),
            quality=()):
    return ViewFinding(i, side, view, BiradsProbabilities.of(*probs), SuspicionScores.of(*g),
                       SuspicionScores.of(*p) if p else None, quality)


def test_probability_out_of_range_is_rejected():
    with pytest.raises(InvalidProbabilityError):
        BiradsProbabilities.of(0.9, 0.2, -0.1)


def test_birads_probabilities_must_add_up_to_one():
    with pytest.raises(InvalidProbabilityError):
        BiradsProbabilities.of(0.5, 0.2, 0.1)


def test_most_likely_group():
    assert BiradsProbabilities.of(0.1, 0.2, 0.7).most_likely() == BiradsGroup.BIOPSY


@pytest.mark.parametrize("scores,expected", [
    ((0.25, 0.9), TriageLevel.HIGH_PRIORITY),
    ((0.10, 0.30), TriageLevel.REVIEW),
    ((0.05, 0.10), TriageLevel.NO_SUSPICIOUS_FINDINGS),
])
def test_triage_policy_levels(scores, expected):
    assert TriagePolicy.level_for(SuspicionScores.of(*scores), THRESHOLDS[ScoringMode.GLOBAL_ONLY]) == expected


def test_breast_requires_views_of_the_same_side():
    with pytest.raises(InvalidBreastAssessmentError):
        BreastAssessment(BreastSide.RIGHT, (finding(0), finding(1, side=BreastSide.LEFT)), "right")


def test_breast_without_patch_scores_uses_global_only_thresholds():
    breast = BreastAssessment(BreastSide.RIGHT, (finding(0, g=(0.21, 0.5)), finding(1, g=(0.19, 0.5))), "right")
    triage = breast.triage(fuse_take_patch, THRESHOLDS)
    assert triage.mode == ScoringMode.GLOBAL_ONLY
    assert triage.scores.suspicion.value == pytest.approx(0.20)
    assert triage.level == TriageLevel.HIGH_PRIORITY


def test_patch_scores_take_the_most_suspicious_view_and_use_fused_thresholds():
    breast = BreastAssessment(BreastSide.LEFT, (finding(0, side=BreastSide.LEFT, p=(0.02, 0.05)),
                                                finding(1, side=BreastSide.LEFT, p=(0.30, 0.40))), "left")
    triage = breast.triage(fuse_take_patch, THRESHOLDS)
    assert triage.mode == ScoringMode.FUSED
    assert triage.scores.suspicion.value == pytest.approx(0.30)
    assert triage.level == TriageLevel.HIGH_PRIORITY


def test_one_low_resolution_view_disables_fusion_for_the_breast():
    breast = BreastAssessment(BreastSide.RIGHT, (finding(0, p=(0.3, 0.4)), finding(1, p=None)), "right")
    assert breast.patch_scores() is None
    assert breast.triage(fuse_take_patch, THRESHOLDS).mode == ScoringMode.GLOBAL_ONLY


def test_study_groups_views_by_side_and_never_combines_unknown_panels():
    study = StudyAssessment.from_findings(
        (finding(0, side=BreastSide.RIGHT), finding(1, side=BreastSide.RIGHT, view=ViewType.MLO),
         finding(2, side=BreastSide.UNKNOWN), finding(3, side=BreastSide.UNKNOWN)), (), 0)
    assert [b.label for b in study.breasts] == ["right", "panel 3", "panel 4"]
    assert len(study.breasts[0].findings) == 2


def test_study_without_mammograms_is_rejected():
    with pytest.raises(NoMammogramFoundError):
        StudyAssessment.from_findings((), (1,), 0)


def test_overall_level_is_the_most_severe():
    study = StudyAssessment.from_findings((finding(0, side=BreastSide.RIGHT, g=(0.05, 0.1)),
                                           finding(1, side=BreastSide.LEFT, g=(0.5, 0.9))), (), 0)
    assert StudyAssessment.overall_level(study.triage(fuse_take_patch, THRESHOLDS)) == TriageLevel.HIGH_PRIORITY


def test_quality_issues_are_collected_once_per_breast():
    breast = BreastAssessment(BreastSide.RIGHT, (finding(0, quality=(QualityIssue.BLURRY,)),
                                                 finding(1, quality=(QualityIssue.BLURRY, QualityIssue.GLARE))),
                              "right")
    assert breast.quality_issues() == (QualityIssue.BLURRY, QualityIssue.GLARE)
