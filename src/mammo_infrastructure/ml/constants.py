"""Input geometry shared by both inference backends (must match how the networks were trained)."""

PATCH, STRIDE, PATCH_HEIGHT = 256, 128, 2048  # patch scanner: 256 px windows every 128 px at 2048 px
GATE_SIZE = 224  # gate input: the image letterboxed to a 224 px square
GATE_MEAN, GATE_STD = 0.449, 0.226  # gate normalization (its checkpoint carries no mean/std)
