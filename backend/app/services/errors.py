"""Typed service errors. The API layer (phase 4B) maps them to HTTP status codes.

Messages are written for the person using the system and are safe to show.
"""


class ServiceError(Exception):
    code = "SERVICE_ERROR"


class NotFound(ServiceError):
    code = "NOT_FOUND"


class PermissionDenied(ServiceError):
    code = "PERMISSION_DENIED"


class InvalidState(ServiceError):
    """The action does not apply to the invoice in its current status."""

    code = "INVALID_STATE"


class ValidationError(ServiceError):
    """The input is missing something or is not acceptable."""

    code = "VALIDATION_ERROR"
