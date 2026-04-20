from __future__ import annotations


class DoryError(Exception):
    """Base error for the Dory domain."""


class DoryConfigError(DoryError):
    """Raised when required runtime configuration is invalid."""


class DoryValidationError(DoryError):
    """Raised when a request fails validation rules."""
