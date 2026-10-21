"""Masking rules shared by both OCR engines, with a fake detector (no OCR models)."""
import numpy as np
import pytest
from PIL import Image

from mammo_infrastructure.ml.text_masking import TILE_OVERLAP, TextMasker, tiles


class FakeDetector:
    def __init__(self, detections, box_margin=1.0, max_side=None):
        self.detections, self.box_margin, self.max_side = detections, box_margin, max_side
        self.seen_shape = None

    def detect(self, rgb):
        self.seen_shape = rgb.shape
        return self.detections


def box(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def gray(width=1600, height=2000, value=128):
    return Image.fromarray(np.full((height, width), value, np.uint8))


def test_masks_confident_text_and_counts_categories_without_returning_it():
    det = FakeDetector([(box(100, 100, 400, 140), "Paciente: 104558231", 0.9),
                        (box(100, 200, 300, 240), "15/03/1968", 0.8)])
    out, regions, cats = TextMasker(det).mask(gray())
    a = np.asarray(out)
    assert regions == 2 and (a[110:130, 150:350] == 0).all() and (a[210:230, 120:280] == 0).all()
    assert cats == {"keyword": 1, "long_number": 1, "date_time": 1}  # categories and counts only, never the text


def test_skips_low_confidence_empty_and_too_tall_detections():
    det = FakeDetector([(box(10, 10, 100, 40), "ID 1234567", 0.2),
                        (box(10, 60, 100, 90), "   ", 0.9),
                        (box(10, 100, 600, 400), "BREAST", 0.9)])  # 300 px tall > 8% of 2000 px
    _, regions, _ = TextMasker(det).mask(gray())
    assert regions == 0


def test_box_margin_lets_engines_with_padded_boxes_keep_large_labels():
    detection = [(box(10, 10, 60, 180), "A", 0.99)]  # 170 px: above 8% of 2000 (160 px) only with its padding
    assert TextMasker(FakeDetector(detection, box_margin=1.0)).mask(gray())[1] == 0
    assert TextMasker(FakeDetector(detection, box_margin=1.15)).mask(gray())[1] == 1


def test_small_images_are_enlarged_and_boxes_mapped_back():
    det = FakeDetector([(box(200, 200, 400, 240), "DER", 0.9)])  # in the x2 image
    out, regions, _ = TextMasker(det).mask(gray(600, 800))
    assert det.seen_shape[:2] == (1600, 1200) and regions == 1
    assert (np.asarray(out)[102:118, 102:198] == 0).all()


def test_large_images_are_read_in_full_resolution_tiles():
    shapes = []

    class Recorder(FakeDetector):
        def detect(self, rgb):
            shapes.append(rgb.shape[:2])
            return []
    TextMasker(Recorder([], max_side=1024)).mask(gray(3000, 4000))
    assert max(max(s) for s in shapes) <= 1024 and len(shapes) == 4 * 5  # read at full resolution, nothing shrunk


@pytest.mark.parametrize("width, height, size", [(3000, 4000, 1024), (1300, 1100, 1024), (900, 700, None)])
def test_tiles_cover_the_whole_image_with_overlap(width, height, size):
    covered = np.zeros((height, width), bool)
    windows = tiles(width, height, size)
    for x0, y0, x1, y1 in windows:
        assert (x1 - x0) <= (size or width) and (y1 - y0) <= (size or height)
        covered[y0:y1, x0:x1] = True
    assert covered.all()
    if size and width > size:
        xs = sorted({w[0] for w in windows})
        assert all(b - a == size - TILE_OVERLAP for a, b in zip(xs, xs[1:-1]))


def test_text_found_in_two_overlapping_tiles_is_masked_and_counted_once(monkeypatch):
    rect = (900, 300, 1000, 330)  # inside the overlap of the first two columns of tiles (824..1024)
    det = FakeDetector([], max_side=1024)
    seen = []

    def located(self_, big):  # emulate the engine: report the text in each tile that contains it
        found = []
        for x0, y0, x1, y1 in tiles(big.shape[1], big.shape[0], 1024):
            if x0 <= rect[0] and rect[2] <= x1 and y0 <= rect[1] and rect[3] <= y1:
                seen.append((x0, y0))
                found.append((box(*rect), "ID 104558231", 0.9))
        return found
    monkeypatch.setattr(TextMasker, "_detect", located)
    out, regions, cats = TextMasker(det).mask(gray(3000, 4000))
    assert len(seen) == 2 and regions == 1 and cats == {"long_number": 1, "keyword": 1}
    assert (np.asarray(out)[300:330, 900:1000] == 0).all()


def test_boxes_found_in_a_tile_are_placed_in_the_full_image():
    class OneTile(FakeDetector):
        def detect(self, rgb):
            self.n = getattr(self, "n", 0) + 1
            return [(box(10, 10, 110, 40), "Paciente", 0.9)] if self.n == 2 else []  # second tile: x0 = 824
    out, regions, _ = TextMasker(OneTile([], max_side=1024)).mask(gray(3000, 4000))
    a = np.asarray(out)
    assert regions == 1 and (a[15:35, 840:930] == 0).all() and (a[15:35, 20:100] == 128).all()
