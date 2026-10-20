"""Use case: cover personal data (any text) drawn over a medical image."""
from __future__ import annotations

from mammo_application.abstractions import ImageDecodingError, PersonalDataMasker
from mammo_application.common import Result, decoding_error, validate_image

from .dtos import ImageQuery, MaskedImageDto


class MaskPersonalDataHandler:
    def __init__(self, masker: PersonalDataMasker, max_image_bytes: int) -> None:
        self._masker = masker
        self._max_image_bytes = max_image_bytes

    def handle(self, query: ImageQuery) -> Result[MaskedImageDto]:
        error = validate_image(query.image, self._max_image_bytes)
        if error:
            return Result(error=error)
        try:
            masked = self._masker.mask(query.image)
        except ImageDecodingError as exc:
            return Result(error=decoding_error(exc))
        return Result.ok(MaskedImageDto(masked.image, masked.masked_regions, dict(masked.region_categories)))
