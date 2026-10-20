"""Use case: tell whether each panel of an image is a usable mammogram."""
from __future__ import annotations

from mammo_application.abstractions import ImageDecodingError, MammogramGate, PanelSplitter, ThresholdsProvider
from mammo_application.common import Result, decoding_error, validate_image

from .dtos import ImageQuery, MammogramCheckDto, PanelCheckDto


class CheckIsMammogramHandler:
    def __init__(self, splitter: PanelSplitter, gate: MammogramGate, versions: ThresholdsProvider,
                 max_image_bytes: int) -> None:
        self._splitter = splitter
        self._gate = gate
        self._versions = versions
        self._max_image_bytes = max_image_bytes

    def handle(self, query: ImageQuery) -> Result[MammogramCheckDto]:
        error = validate_image(query.image, self._max_image_bytes)
        if error:
            return Result(error=error)
        try:
            panels = self._splitter.split(query.image, query.panel_count)
        except ImageDecodingError as exc:
            return Result(error=decoding_error(exc))
        checks = []
        for i, panel in enumerate(panels):
            v = self._gate.check(panel)
            checks.append(PanelCheckDto(i + 1, v.is_mammogram, round(v.confidence, 3),
                                        [q.value for q in v.quality_issues]))
        return Result.ok(MammogramCheckDto(checks, self._versions.model_version()))
