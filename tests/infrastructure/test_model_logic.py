"""Backend-independent model logic with fake networks (no weights, no PyTorch, no ONNX Runtime)."""
import numpy as np
import pytest
from PIL import Image

from mammo_infrastructure.ml.classifiers import GlobalEnsemble, PatchScanner
from mammo_infrastructure.ml.constants import PATCH, STRIDE
from mammo_infrastructure.ml.gate_model import GateModel
from mammo_infrastructure.ml.inference import LoadedNetwork, normalize, softmax


class BrightnessNetwork:
    """P(4-5) grows with the tile's mean brightness; records the batch sizes it receives."""

    def __init__(self):
        self.batches = []

    def probabilities(self, batch):
        self.batches.append(len(batch))
        m = batch.reshape(len(batch), -1).mean(1) / 255
        return np.stack([1 - m, np.zeros_like(m), m], axis=1)


def breast(seed=0):
    rng = np.random.default_rng(seed)
    a = np.zeros((2400, 1900), np.uint8)
    a[150:2250, :1500] = rng.integers(60, 200, (2100, 1500))  # tissue against the chest wall (left)
    a[900:1100, 700:900] = 255  # one bright "lesion"
    return a


@pytest.mark.parametrize("batch_size", [1, 7, 96])
def test_patch_scores_do_not_depend_on_the_batch_size(batch_size):
    reference = PatchScanner(BrightnessNetwork(), 96).scan(breast())
    net = BrightnessNetwork()
    p45, lesion, heat, processed = PatchScanner(net, batch_size).scan(breast())
    assert (p45, lesion) == pytest.approx(reference[:2])
    np.testing.assert_array_equal(heat, reference[2])
    assert max(net.batches) <= batch_size


def test_patch_scan_puts_the_brightest_window_in_the_heat_grid():
    p45, lesion, heat, processed = PatchScanner(BrightnessNetwork(), 16).scan(breast())
    iy, ix = np.unravel_index(np.argmax(heat), heat.shape)
    window = processed[iy * STRIDE:iy * STRIDE + PATCH, ix * STRIDE:ix * STRIDE + PATCH]
    assert window.mean() / 255 == pytest.approx(heat.max(), abs=1e-6)
    assert p45 == pytest.approx(np.sort(heat.ravel())[-2:].mean(), abs=1e-6)


def test_no_tissue_gives_zero_scores():
    p45, lesion, heat, _ = PatchScanner(BrightnessNetwork(), 16).scan(np.zeros((2400, 1900), np.uint8))
    assert (p45, lesion) == (0.0, 0.0) and not heat.any()


class FixedNetwork:
    def __init__(self, p):
        self.p, self.shapes = np.asarray(p, np.float32), []

    def probabilities(self, batch):
        self.shapes.append(batch.shape)
        return np.tile(self.p, (len(batch), 1))


def test_global_ensemble_averages_each_network_at_its_own_height():
    a, b = FixedNetwork([0.6, 0.3, 0.1]), FixedNetwork([0.2, 0.3, 0.5])
    p = GlobalEnsemble([(a, 512), (b, 1024)]).probabilities(breast())
    np.testing.assert_allclose(p, [0.4, 0.3, 0.3], atol=1e-6)
    assert a.shapes[0][:2] == (1, 512) and b.shapes[0][:2] == (1, 1024)


def test_gate_averages_three_variants_and_reads_its_threshold():
    net = FixedNetwork([0.2, 0.8])
    gate = GateModel(LoadedNetwork(net, {"umbral": 0.5, "calidad": {}}))
    assert gate.p_mammogram(Image.fromarray(breast())) == pytest.approx(0.8)
    assert gate.threshold == 0.5 and net.shapes[0] == (3, 224, 224)


def test_softmax_and_normalization_match_training():
    np.testing.assert_allclose(softmax(np.array([[0.0, np.log(3.0)]])), [[0.25, 0.75]])
    x = normalize(np.array([[[0, 255]]], np.uint8), 0.5, 0.25)
    assert x.shape == (1, 1, 1, 2) and x.dtype == np.float32
    np.testing.assert_allclose(x[0, 0, 0], [-2.0, 2.0])
