"""Result type for expected application errors (no exceptions for control flow)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


class ErrorCode(Enum):
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    IMAGE_TOO_LARGE = "image_too_large"
    UNREADABLE_IMAGE = "unreadable_image"
    NOT_A_MAMMOGRAM = "not_a_mammogram"


@dataclass(frozen=True)
class AppError:
    code: ErrorCode
    message: str
    details: dict | None = None


@dataclass(frozen=True)
class Result(Generic[T]):
    value: T | None = None
    error: AppError | None = None

    @property
    def is_success(self) -> bool:
        return self.error is None

    @staticmethod
    def ok(value: T) -> Result[T]:
        return Result(value=value)

    @staticmethod
    def fail(code: ErrorCode, message: str, details: dict | None = None) -> Result[T]:
        return Result(error=AppError(code, message, details))
