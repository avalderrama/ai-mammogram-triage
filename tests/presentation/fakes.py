"""Fake use case handlers returning canned Results (no ML)."""
from mammo_application.abstractions import EncodedImage
from mammo_application.analyses import (AnalysisResultDto, BreastResultDto, MammogramCheckDto, MaskedImageDto,
                                        PanelCheckDto, ViewResultDto)
from mammo_application.common import ErrorCode, Result
from mammo_mcp import McpHandlers

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
HEATMAP = EncodedImage(b"\x89PNG\r\n\x1a\nheat", "image/png")
OVERLAY = EncodedImage(b"\x89PNG\r\n\x1a\nred", "image/png")


def analysis(heatmap=True, layers=False) -> AnalysisResultDto:
    view = ViewResultDto(1, "R", "MLO", {"birads_1_2": 0.1, "birads_3": 0.1, "birads_4_5": 0.8}, ["blurry"],
                         HEATMAP if heatmap else None, None if heatmap else "low_resolution",
                         heatmap_overlay=OVERLAY if heatmap and layers else None)
    breast = BreastResultDto("R", "R", "high_priority", "fused", 0.9, 0.95, "BI-RADS 4-5",
                             {"birads_1_2": 0.1, "birads_3": 0.1, "birads_4_5": 0.8}, ["blurry"], [view])
    return AnalysisResultDto("high_priority", [breast], [], 2, "1.0.0")


class Recorder:
    def __init__(self, result):
        self.result, self.calls = result, []

    def handle(self, arg):
        self.calls.append(arg)
        return self.result


def handlers(analyze=None, check=None, mask=None) -> McpHandlers:
    return McpHandlers(
        analyze=Recorder(analyze or Result.ok(analysis())),
        check=Recorder(check or Result.ok(MammogramCheckDto([PanelCheckDto(1, True, 0.98, [])], "1.0.0"))),
        mask=Recorder(mask or Result.ok(MaskedImageDto(EncodedImage(PNG_BYTES, "image/png"), 3, {"date": 1}))),
    )


NOT_MAMMOGRAM = Result.fail(ErrorCode.NOT_A_MAMMOGRAM, "No panel looks like a mammogram.",
                            {"rejected_panels": [{"panel": 1, "mammogram_confidence": 0.02}]})
