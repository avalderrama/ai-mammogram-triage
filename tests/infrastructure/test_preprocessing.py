"""When preprocessing inverts an image: only true negatives, never a mammogram beside something bright."""
import numpy as np

from mammo_infrastructure.ml.preprocessing import is_negative, preprocess


def mammogram(background=0, height=1200, width=900, seed=0):
    """A breast (textured gray) against the chest wall on the left, on the given background."""
    rng = np.random.default_rng(seed)
    image = np.full((height, width), background, np.uint8)
    yy, xx = np.mgrid[:height, :width]
    breast = ((xx / (0.6 * width)) ** 2 + ((yy - height / 2) / (0.45 * height)) ** 2) < 1
    image[breast] = rng.integers(70, 200, breast.sum())
    return image


def test_a_digital_mammogram_is_not_inverted():
    assert not is_negative(mammogram())


def test_a_negative_is_inverted_back():
    negative = 255 - mammogram()
    assert is_negative(negative)
    # The breast is found again and ends up brighter than the background, as in the positive.
    out = preprocess(negative)
    assert out[:, : out.shape[1] // 3].mean() > 60


def test_a_white_block_beside_the_breast_is_not_a_negative():
    image = mammogram()
    image[:, -250:] = 255  # a label or a scanner block on one side
    assert not is_negative(image)


def test_a_monitor_photo_with_gray_black_and_white_strips_is_not_a_negative():
    # The screen's black comes out gray in a phone photo, and the crop keeps the viewer's white strips at the sides.
    photo = mammogram(background=95)
    photo[:, :60] = 245
    photo[:, -60:] = 245
    assert not is_negative(photo)
    out = preprocess(photo)
    assert out[:, : out.shape[1] // 3].mean() > 60  # tissue stays bright: the view was not turned into a negative
