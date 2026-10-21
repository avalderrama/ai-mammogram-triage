"""The ONNX Runtime backend gives the same results as PyTorch on CPU. Run with `pytest -m integration`.

Needs MAMMO_TORCH_MODELS_DIR (PyTorch bundle), MAMMO_ONNX_MODELS_DIR (the same bundle exported with export_onnx.py)
and MAMMO_TEST_IMAGES_DIR (local test library, never committed). PyTorch runs on CPU in float32, so both backends
compute the same thing and only floating-point rounding differs.
"""
import io
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

pytestmark = pytest.mark.integration

TORCH_DIR, ONNX_DIR = os.environ.get("MAMMO_TORCH_MODELS_DIR"), os.environ.get("MAMMO_ONNX_MODELS_DIR")
IMAGES = os.environ.get("MAMMO_TEST_IMAGES_DIR")
if not (TORCH_DIR and ONNX_DIR and IMAGES):
    pytest.skip("MAMMO_TORCH_MODELS_DIR, MAMMO_ONNX_MODELS_DIR and MAMMO_TEST_IMAGES_DIR are required",
                allow_module_level=True)

from mammo_application.abstractions import EncodedImage  # noqa: E402
from mammo_infrastructure import create_ports  # noqa: E402

LIBRARY = Path(IMAGES) / "imagenes"
# Low-resolution figures and a viewer capture (global models only) and views of at least 1500 px, where the patch
# scanner, the heatmap and the regions run too. "@1.25" is the image enlarged x1.25 (rmlo_masa is 1333 px high).
CASES = ["rmlo_masa.png", "rcc_masa.png", "mlo_densa.png", "mlo_grasa.png", "cc_der_izq.png",
         "siemens_wide_angle_masa.webp", "captura_visor_1.png", "pmc13191622_rcc_phyllodes.webp"]
TALL_CASES = ["pmc13652519_rcc_cdis_alto_grado_en_fibroadenoma.webp", "rmlo_masa.png@1.25"]
TOLERANCE = 1e-3
# Outputs of the PyTorch code before the backends were split (commit a2faf9a, CPU): the shared numpy code
# (preprocessing, patch scan, regions) must keep giving them with BOTH backends. (gate, [P(1-2), P(3), P(4-5)],
# patch (suspicion, attention), top region (x, y)).
REFERENCE = {
    "rmlo_masa.png": (1.0, [0.0207, 0.0513, 0.9279], None, None),
    "pmc13652519_rcc_cdis_alto_grado_en_fibroadenoma.webp": (1.0, [0.7151, 0.0937, 0.1912], (0.3032, 0.7908),
                                                            (0.1538, 0.375)),
}


@pytest.fixture(scope="module")
def backends():
    return create_ports(Path(TORCH_DIR), "cpu"), create_ports(Path(ONNX_DIR), "auto")


def encoded(case: str) -> EncodedImage:
    name, _, factor = case.partition("@")
    if not factor:
        return EncodedImage((LIBRARY / name).read_bytes(), "image/png")
    img = Image.open(LIBRARY / name)
    img = img.resize((round(img.width * float(factor)), round(img.height * float(factor))), Image.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return EncodedImage(buf.getvalue(), "image/png")


def probabilities(prediction) -> list[float]:
    p = prediction.probabilities
    return [p.routine.value, p.short_follow_up.value, p.biopsy.value]


def compare(backends, case: str) -> int:
    """Asserts both backends agree on every panel; returns how many panels went through the patch scanner."""
    torch_ports, onnx_ports = backends
    panels = torch_ports.splitter.split(encoded(case), None)
    assert panels == onnx_ports.splitter.split(encoded(case), None)
    scanned = 0
    for panel in panels:
        gt, go = torch_ports.gate.check(panel), onnx_ports.gate.check(panel)
        assert gt.is_mammogram == go.is_mammogram and gt.quality_issues == go.quality_issues
        assert gt.confidence == pytest.approx(go.confidence, abs=TOLERANCE)
        if not gt.is_mammogram:
            continue
        pt, po = torch_ports.classifier.predict(panel, True), onnx_ports.classifier.predict(panel, True)
        np.testing.assert_allclose(probabilities(pt), probabilities(po), atol=TOLERANCE)
        assert pt.global_scores.suspicion.value == pytest.approx(po.global_scores.suspicion.value, abs=TOLERANCE)
        assert pt.global_scores.attention.value == pytest.approx(po.global_scores.attention.value, abs=TOLERANCE)
        assert (pt.patch_scores is None) == (po.patch_scores is None)
        if pt.patch_scores is not None:
            scanned += 1
            assert pt.patch_scores.suspicion.value == pytest.approx(po.patch_scores.suspicion.value, abs=TOLERANCE)
            assert pt.patch_scores.attention.value == pytest.approx(po.patch_scores.attention.value, abs=TOLERANCE)
            assert [(r.x, r.y) for r in pt.regions] == [(r.x, r.y) for r in po.regions]
            assert [r.score for r in pt.regions] == pytest.approx([r.score for r in po.regions], abs=TOLERANCE)
            assert (pt.heatmap is None) == (po.heatmap is None)
    return scanned


@pytest.mark.parametrize("case", CASES)
def test_gate_and_global_classifier_match(backends, case):
    compare(backends, case)


@pytest.mark.parametrize("case", TALL_CASES)
def test_patch_scanner_heatmap_and_regions_match(backends, case):
    assert compare(backends, case) >= 1, "the patch scanner did not run: this case no longer tests it"


@pytest.mark.parametrize("backend", [0, 1], ids=["torch", "onnx"])
@pytest.mark.parametrize("name", list(REFERENCE))
def test_both_backends_keep_the_reference_outputs(backends, backend, name):
    ports = backends[backend]
    gate, probs, patch, region = REFERENCE[name]
    panel = ports.splitter.split(encoded(name), None)[0]
    assert ports.gate.check(panel).confidence == pytest.approx(gate, abs=TOLERANCE)
    prediction = ports.classifier.predict(panel, False)
    np.testing.assert_allclose(probabilities(prediction), probs, atol=TOLERANCE)
    if patch is None:
        assert prediction.patch_scores is None
    else:
        scores = prediction.patch_scores
        assert (scores.suspicion.value, scores.attention.value) == pytest.approx(patch, abs=TOLERANCE)
        assert (prediction.regions[0].x, prediction.regions[0].y) == region


FAKE_LINES = ["PACIENTE: MARIA LOPEZ GARCIA", "ID 104558231", "F. NAC. 15/03/1968"]


def with_fake_personal_data(case: str, text_height: float) -> tuple[EncodedImage, list[tuple[int, int, int, int]]]:
    img = Image.open(io.BytesIO(encoded(case).data)).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=max(round(img.height * text_height), 12))
    y, boxes = img.height // 25, []
    for line in FAKE_LINES:
        x = img.width - int(draw.textlength(line, font=font)) - img.width // 30
        boxes.append(draw.textbbox((x, y), line, font=font))
        draw.text((x, y), line, fill=(235, 235, 235), font=font)
        y += int(font.size * 1.6)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return EncodedImage(buf.getvalue(), "image/png"), boxes


@pytest.mark.parametrize("case, text_height", [("rcc_masa.png", 1 / 45), ("mlo_densa.png", 1 / 45),
                                               ("rmlo_masa.png@3", 1 / 250)])  # 4000 px high, 16 px text
def test_onnx_masker_hides_personal_data(backends, case, text_height):
    _, onnx_ports = backends
    image, boxes = with_fake_personal_data(case, text_height)
    masked = onnx_ports.masker.mask(image)
    out = np.asarray(Image.open(io.BytesIO(masked.image.data)).convert("L"))
    for x0, y0, x1, y1 in boxes:
        assert (out[y0:y1, x0:x1] == 0).mean() >= 0.95, "a line of personal data was left visible"
    assert masked.masked_regions >= len(FAKE_LINES)
