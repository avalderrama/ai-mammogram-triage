"""Gate v2: black-level normalization, versions the server refuses, and the shadow gate that only observes."""
import io
import threading

import numpy as np
import pytest
from PIL import Image

from mammo_application.abstractions import EncodedImage
from mammo_infrastructure import JsonUsageLog
from mammo_infrastructure._adapters import _MammogramGate
from mammo_infrastructure.bundle import BundleError
from mammo_infrastructure.ml.gate_model import GateModel, normalize_black
from mammo_infrastructure.ml.inference import LoadedNetwork

BLACK_LEVEL = {"percentile": 1, "min": 12}
QUALITY = {"min_lado_px": 10, "nitidez_min": 0, "saturado_max": 1, "mama_min_fraccion": 0}


class RecordingNetwork:
    """Answers a fixed P(mammogram) and keeps the mean brightness of what it was given."""

    def __init__(self, p):
        self.p, self.brightness = p, []

    def probabilities(self, batch):
        self.brightness.append(float(batch[0].mean()))
        return np.tile([1 - self.p, self.p], (len(batch), 1))


def gate(p=0.9, **meta):
    network = RecordingNetwork(p)
    return GateModel(LoadedNetwork(network, {"umbral": 0.5, "calidad": QUALITY, **meta})), network


def photographed_monitor(black=60):
    """A breast on a black background that a phone photo turned grey (black ~60)."""
    image = np.full((400, 300), black, np.uint8)
    image[50:350, :200] = 180
    return Image.fromarray(image)


def test_normalize_black_stretches_a_grey_black_and_leaves_digital_images_alone():
    stretched = np.asarray(normalize_black(photographed_monitor(60), BLACK_LEVEL))
    assert stretched.min() == 0 and stretched.max() == int((180 - 60) * 255 / 195)
    digital = photographed_monitor(0)
    assert np.array_equal(np.asarray(normalize_black(digital, BLACK_LEVEL)), np.asarray(digital))


def test_gate_v2_normalizes_before_judging_and_v1_does_not():
    v2, v2_net = gate(version="2", black_level=BLACK_LEVEL)
    v1, v1_net = gate()
    v2.p_mammogram(photographed_monitor())
    v1.p_mammogram(photographed_monitor())
    assert v2_net.brightness[0] < v1_net.brightness[0]  # the grey black is brought back to 0 only by v2


@pytest.mark.parametrize("meta", [{"version": "3"}, {"version": "2"}, {"version": "2", "black_level": {"min": 12}}])
def test_gates_this_server_cannot_run_correctly_are_refused(meta):
    with pytest.raises(BundleError):
        gate(**meta)


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return EncodedImage(buffer.getvalue(), "image/png")


def test_the_shadow_gate_is_compared_but_never_decides(tmp_path):
    usage = JsonUsageLog(tmp_path / "usage.json", b"k" * 32, today=lambda: "2026-10-23")
    current, _ = gate(p=0.2)
    shadow, _ = gate(p=0.9, version="2", black_level=BLACK_LEVEL)
    verdict = _MammogramGate(current, threading.Lock(), shadow, usage.record_gate_comparison).check(
        png(photographed_monitor()))
    assert verdict.is_mammogram is False  # the gate in use decides
    day = usage.daily(1)[0]
    assert (day.gate_compared, day.gate_only_current, day.gate_only_shadow) == (1, 0, 1)


def test_without_a_shadow_gate_nothing_is_compared(tmp_path):
    usage = JsonUsageLog(tmp_path / "usage.json", b"k" * 32, today=lambda: "2026-10-23")
    current, _ = gate(p=0.9)
    assert _MammogramGate(current, threading.Lock()).check(png(photographed_monitor())).is_mammogram is True
    assert usage.daily(1) == []
