"""Typed business errors of the assessment domain."""


class DomainError(Exception):
    """Base class for business rule violations."""


class InvalidProbabilityError(DomainError):
    pass


class InvalidThresholdsError(DomainError):
    pass


class InvalidBreastAssessmentError(DomainError):
    pass


class NoMammogramFoundError(DomainError):
    """The study has no panel accepted as a mammogram."""
