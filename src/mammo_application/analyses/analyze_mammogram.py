"""Use case: analyze a mammogram image and triage each breast."""
from __future__ import annotations

from dataclasses import asdict

from mammo_application.abstractions import (BiradsClassifier, ImageDecodingError, MammogramGate, PanelSplitter,
                                            PersonalDataMasker, ScoreFuser, ThresholdsProvider, UsageEvent, UsageLog)
from mammo_application.common import ErrorCode, Result, arrangement_error, decoding_error, validate_image
from mammo_domain.assessment import (BiradsProbabilities, BreastSide, NoMammogramFoundError, StudyAssessment,
                                     ViewFinding, ViewType)

from .dtos import AnalysisResultDto, AnalyzeMammogramCommand, BreastResultDto, RejectedPanelDto, ViewResultDto

MAX_PANELS = 8


def _usage_event(result: Result[AnalysisResultDto], requester: str | None) -> UsageEvent:
    if not result.is_success:
        outcome = "not_a_mammogram" if result.error.code == ErrorCode.NOT_A_MAMMOGRAM else "error"
        return UsageEvent(outcome, requester=requester)
    views = [v for b in result.value.breasts for v in b.views]
    return UsageEvent(result.value.overall_level, views=len(views),
                      detailed_views=sum(v.heatmap_unavailable_reason is None for v in views), requester=requester)


def _probabilities_dict(p: BiradsProbabilities) -> dict[str, float]:
    return {"birads_1_2": p.routine.value, "birads_3": p.short_follow_up.value, "birads_4_5": p.biopsy.value}


class AnalyzeMammogramHandler:
    def __init__(self, masker: PersonalDataMasker, splitter: PanelSplitter, gate: MammogramGate,
                 classifier: BiradsClassifier, fuser: ScoreFuser, thresholds: ThresholdsProvider,
                 max_image_bytes: int, usage: UsageLog | None = None) -> None:
        self._masker = masker
        self._splitter = splitter
        self._gate = gate
        self._classifier = classifier
        self._fuser = fuser
        self._thresholds = thresholds
        self._max_image_bytes = max_image_bytes
        self._usage = usage

    def handle(self, command: AnalyzeMammogramCommand) -> Result[AnalysisResultDto]:
        try:
            result = self._analyze(command)
        except Exception:
            if self._usage is not None:
                self._usage.record(UsageEvent("error", requester=command.requester))
            raise
        if self._usage is not None:
            self._usage.record(_usage_event(result, command.requester))
        return result

    def _analyze(self, command: AnalyzeMammogramCommand) -> Result[AnalysisResultDto]:
        error = validate_image(command.image, self._max_image_bytes)
        if error:
            return Result(error=error)
        if command.panel_count is not None and not 1 <= command.panel_count <= MAX_PANELS:
            return Result.fail(ErrorCode.INVALID_INPUT, f"panel_count must be between 1 and {MAX_PANELS}.")
        error = arrangement_error(command.panel_count, command.panel_arrangement)
        if error:
            return Result(error=error)
        try:
            masked = self._masker.mask(command.image)
            panels = self._splitter.split(masked.image, command.panel_count, command.panel_arrangement == "grid")
        except ImageDecodingError as exc:
            return Result(error=decoding_error(exc))
        sides, views, layout_error = self._layout(command, len(panels))
        if layout_error:
            return Result.fail(ErrorCode.INVALID_INPUT, layout_error)

        findings: list[ViewFinding] = []
        rejected: list[RejectedPanelDto] = []
        predictions = {}
        for i, panel in enumerate(panels):
            verdict = self._gate.check(panel)
            if not verdict.is_mammogram:
                rejected.append(RejectedPanelDto(i + 1, round(verdict.confidence, 3)))
                continue
            pred = self._classifier.predict(panel, command.include_heatmap, command.heatmap_format == "layers")
            predictions[i] = pred
            findings.append(ViewFinding(i, sides[i], views[i], pred.probabilities, pred.global_scores,
                                        pred.patch_scores, verdict.quality_issues))
        try:
            study = StudyAssessment.from_findings(tuple(findings), tuple(r.panel for r in rejected),
                                                  masked.masked_regions)
        except NoMammogramFoundError:
            return Result.fail(ErrorCode.NOT_A_MAMMOGRAM,
                               "The image does not look like a mammogram (CC or MLO view). "
                               "Ultrasound, MRI, tomosynthesis slices and other images are not supported.",
                               {"panels": [{"panel": r.panel, "mammogram_confidence": r.mammogram_confidence}
                                           for r in rejected]})

        triages = study.triage(self._fuser.fuse, self._thresholds.thresholds())
        breasts = []
        for breast, triage in zip(study.breasts, triages):
            view_dtos = [ViewResultDto(
                panel=f.panel_index + 1, side=f.side.value, view=f.view.value,
                probabilities=_probabilities_dict(f.probabilities),
                quality_issues=[q.value for q in f.quality_issues],
                heatmap=predictions[f.panel_index].heatmap,
                heatmap_unavailable_reason=predictions[f.panel_index].patch_unavailable_reason,
                regions=[asdict(r) for r in predictions[f.panel_index].regions],
                heatmap_overlay=predictions[f.panel_index].heatmap_overlay)
                for f in breast.findings]
            probs = breast.probabilities()
            breasts.append(BreastResultDto(
                label=breast.label, side=breast.side.value, level=triage.level.value,
                scoring_mode=triage.mode.value, suspicion=round(triage.scores.suspicion.value, 4),
                attention=round(triage.scores.attention.value, 4), most_likely=probs.most_likely().value,
                probabilities=_probabilities_dict(probs), quality_issues=[q.value for q in breast.quality_issues()],
                views=view_dtos))
        return Result.ok(AnalysisResultDto(
            overall_level=StudyAssessment.overall_level(triages).value, breasts=breasts, rejected_panels=rejected,
            masked_text_regions=study.masked_text_regions, model_version=self._thresholds.model_version()))

    @staticmethod
    def _layout(command: AnalyzeMammogramCommand, n: int):
        def parse(values, enum, name):
            if values is None:
                return [enum("unknown")] * n, None
            if len(values) != n:
                return None, f"{name} has {len(values)} values but the image has {n} panel(s)."
            try:
                return [enum(v if v in ("R", "L", "CC", "MLO") else v.lower()) for v in values], None
            except ValueError:
                return None, f"Invalid value in {name}: {list(values)}."
        sides, err = parse(command.sides, BreastSide, "sides")
        if err:
            return None, None, err
        views, err = parse(command.views, ViewType, "views")
        return sides, views, err
