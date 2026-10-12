"""Typed application errors mapped onto HTTP status codes by the API layer."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every error the API is willing to expose to clients."""

    code = "internal_error"
    status = 500
    #: Seconds the caller should wait before retrying (``None`` = no hint).
    retry_after: float | None = None

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.details:
            payload["error"]["details"] = self.details
        return payload


class ValidationError(AppError):
    code = "validation_error"
    status = 400


class NotFoundError(AppError):
    code = "not_found"
    status = 404


class ComplianceError(AppError):
    """Raised when a request would violate the personal-use-only policy."""

    code = "compliance_violation"
    status = 403


class RateLimitedError(AppError):
    code = "rate_limited"
    status = 429

    def __init__(
        self, message: str, *, retry_after: float, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, details=details)
        self.retry_after = retry_after


class UpstreamError(AppError):
    """A music platform or yt-dlp failed us."""

    code = "upstream_error"
    status = 502


class UpstreamBlockedError(UpstreamError):
    """The platform's risk control rejected us (e.g. Bilibili HTTP 412).

    Retrying immediately makes it worse, so the message tells the caller to
    wait and how to make the next attempt more likely to succeed.
    """

    code = "upstream_blocked"
    status = 503

    def __init__(
        self, message: str, *, retry_after: float = 90.0, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, details=details)
        self.retry_after = retry_after
        self.details.setdefault("retryAfterSeconds", retry_after)


class ExtractorUnavailableError(AppError):
    code = "extractor_unavailable"
    status = 503


__all__ = [
    "AppError",
    "ComplianceError",
    "ExtractorUnavailableError",
    "NotFoundError",
    "RateLimitedError",
    "UpstreamBlockedError",
    "UpstreamError",
    "ValidationError",
]
