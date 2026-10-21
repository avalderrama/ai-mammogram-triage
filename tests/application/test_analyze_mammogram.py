"""Use case tests with fake ports (no ML, no IO)."""
from mammo_application.abstractions import (EncodedImage, GateVerdict, ImageDecodingError, MaskedImage,
                                            PanelPrediction, SuspiciousRegion)
from mammo_application.analyses import AnalyzeMammogramCommand, AnalyzeMammogramHandler
from mammo_application.common import ErrorCode
from mammo_domain.assessment import (BiradsProbabilities, QualityIssue, ScoringMode, SuspicionScores,
                                     TriageThresholds)

PNG = EncodedImage(b"\x89PNG fake", "image/png")


class FakeMasker:
    def __init__(self):
        self.calls = []

    def mask(self, image):
        self.calls.append(image)
        return MaskedImage(EncodedImage(b"masked", "image/png"), 2, {"date": 1})


class FakeSplitter:
    def __init__(self, n=2, fail=False):
        self.n, self.fail, self.received = n, fail, None

    def split(self, image, panel_count):
        if self.fail:
            raise ImageDecodingError()
        self.received = image
        return [EncodedImage(f"panel{i}".encode(), "image/png") for i in range(panel_count or self.n)]


class FakeGate:
    def __init__(self, accepted=(True, True)):
        self.accepted = list(accepted)

    def check(self, panel):
        ok = self.accepted.pop(0)
        return GateVerdict(ok, 0.99 if ok else 0.01, (QualityIssue.BLURRY,) if ok else ())


class FakeClassifier:
    def __init__(self, patch=True):
        self.patch, self.heatmap_requested = patch, []

    def predict(self, panel, with_heatmap):
        self.heatmap_requested.append(with_heatmap)
        return PanelPrediction(BiradsProbabilities.of(0.2, 0.2, 0.6), SuspicionScores.of(0.6, 0.8),
                               SuspicionScores.of(0.7, 0.9) if self.patch else None,
                               EncodedImage(b"heat", "image/png") if with_heatmap and self.patch else None,
                               None if self.patch else "low_resolution",
                               regions=(SuspiciousRegion(0.6, 0.1, 0.15, 0.12, 0.9, "anterior", "upper"),)
                               if self.patch else ())


class FakeFuser:
    def fuse(self, g, p):
        return SuspicionScores.of((g.suspicion.value + p.suspicion.value) / 2,
                                  (g.attention.value + p.attention.value) / 2)


class FakeThresholds:
    def thresholds(self):
        return {ScoringMode.FUSED: TriageThresholds.of(0.08, 0.08),
                ScoringMode.GLOBAL_ONLY: TriageThresholds.of(0.2, 0.25)}

    def model_version(self):
        return "test-1"


def handler(masker=None, splitter=None, gate=None, classifier=None, max_bytes=1000):
    return AnalyzeMammogramHandler(masker or FakeMasker(), splitter or FakeSplitter(), gate or FakeGate(),
                                   classifier or FakeClassifier(), FakeFuser(), FakeThresholds(), max_bytes)


def test_personal_data_is_masked_before_anything_else():
    masker, splitter = FakeMasker(), FakeSplitter()
    result = handler(masker=masker, splitter=splitter).handle(AnalyzeMammogramCommand(PNG))
    assert result.is_success
    assert splitter.received.data == b"masked"
    assert result.value.masked_text_regions == 2


def test_views_of_the_same_side_are_triaged_together():
    cmd = AnalyzeMammogramCommand(PNG, panel_count=2, sides=("R", "R"), views=("CC", "MLO"))
    result = handler().handle(cmd)
    assert result.is_success
    assert len(result.value.breasts) == 1
    breast = result.value.breasts[0]
    assert breast.side == "R" and breast.scoring_mode == "fused" and breast.level == "high_priority"
    assert [v.view for v in breast.views] == ["CC", "MLO"]


def test_non_mammogram_panels_are_reported_and_not_classified():
    classifier = FakeClassifier()
    result = handler(gate=FakeGate((False, True)), classifier=classifier).handle(AnalyzeMammogramCommand(PNG))
    assert result.is_success
    assert [r.panel for r in result.value.rejected_panels] == [1]
    assert len(classifier.heatmap_requested) == 1


def test_image_without_any_mammogram_returns_not_a_mammogram():
    result = handler(gate=FakeGate((False, False))).handle(AnalyzeMammogramCommand(PNG))
    assert not result.is_success
    assert result.error.code == ErrorCode.NOT_A_MAMMOGRAM
    assert len(result.error.details["panels"]) == 2


def test_oversized_image_is_rejected_before_processing():
    masker = FakeMasker()
    result = handler(masker=masker, max_bytes=3).handle(AnalyzeMammogramCommand(PNG))
    assert result.error.code == ErrorCode.IMAGE_TOO_LARGE
    assert masker.calls == []


def test_unsupported_media_type_is_rejected():
    result = handler().handle(AnalyzeMammogramCommand(EncodedImage(b"x", "application/pdf")))
    assert result.error.code == ErrorCode.UNSUPPORTED_MEDIA_TYPE


def test_unreadable_image_returns_error():
    result = handler(splitter=FakeSplitter(fail=True)).handle(AnalyzeMammogramCommand(PNG))
    assert result.error.code == ErrorCode.UNREADABLE_IMAGE


def test_sides_must_match_the_number_of_panels():
    result = handler().handle(AnalyzeMammogramCommand(PNG, sides=("R",)))
    assert result.error.code == ErrorCode.INVALID_INPUT


def test_heatmap_is_not_requested_when_disabled():
    classifier = FakeClassifier()
    handler(classifier=classifier).handle(AnalyzeMammogramCommand(PNG, include_heatmap=False))
    assert classifier.heatmap_requested == [False, False]


def test_low_resolution_explains_why_there_is_no_heatmap():
    result = handler(classifier=FakeClassifier(patch=False)).handle(AnalyzeMammogramCommand(PNG))
    view = result.value.breasts[0].views[0]
    assert view.heatmap is None and view.heatmap_unavailable_reason == "low_resolution"
    assert result.value.breasts[0].scoring_mode == "global_only"


def test_too_many_pixels_is_reported_as_too_large():
    from mammo_application.abstractions import ImageTooManyPixelsError

    class HugeSplitter(FakeSplitter):
        def split(self, image, panel_count):
            raise ImageTooManyPixelsError()

    handler = AnalyzeMammogramHandler(FakeMasker(), HugeSplitter(), FakeGate(), FakeClassifier(), FakeFuser(),
                                      FakeThresholds(), 1_000_000)
    result = handler.handle(AnalyzeMammogramCommand(PNG))
    assert result.error.code == ErrorCode.IMAGE_TOO_LARGE


def test_suspicious_regions_reach_the_view_result():
    handler = AnalyzeMammogramHandler(FakeMasker(), FakeSplitter(n=1), FakeGate(accepted=(True,)), FakeClassifier(),
                                      FakeFuser(), FakeThresholds(), 1_000_000)
    view = handler.handle(AnalyzeMammogramCommand(PNG, sides=("R",), views=("MLO",), panel_count=1)).value.breasts[0].views[0]
    assert view.regions == [{"x": 0.6, "y": 0.1, "width": 0.15, "height": 0.12, "score": 0.9,
                             "depth": "anterior", "vertical": "upper"}]
