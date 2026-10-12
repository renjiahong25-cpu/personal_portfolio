"""Shared HTTP plumbing: JSON bodies, CORS for the Vite dev server, error mapping."""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import tornado.web

from container import Services
from core.errors import AppError, ValidationError

logger = logging.getLogger(__name__)

Handler = Callable[..., Awaitable[None]]

#: The frontend dev server origin. Production builds are served by Tornado
#: itself, so same-origin requests never need CORS.
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def api_endpoint(func: Handler) -> Handler:
    """Translate domain errors into JSON responses; never leak a traceback."""

    @functools.wraps(func)
    async def wrapper(self: BaseHandler, *args: Any, **kwargs: Any) -> None:
        try:
            await func(self, *args, **kwargs)
        except AppError as exc:
            retry_after = getattr(exc, "retry_after", None)
            if retry_after is not None:
                self.set_header("Retry-After", str(int(retry_after) + 1))
            self.set_status(exc.status)
            self.write_json(exc.to_dict())
        except json.JSONDecodeError as exc:
            self.set_status(400)
            self.write_json(ValidationError(f"malformed JSON body: {exc}").to_dict())
        except tornado.web.HTTPError:
            raise
        except Exception:
            logger.exception("unhandled error in %s", func.__qualname__)
            self.set_status(500)
            self.write_json(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "internal server error",
                    }
                }
            )

    return wrapper


class BaseHandler(tornado.web.RequestHandler):
    @property
    def services(self) -> Services:
        return self.settings["services"]

    def set_default_headers(self) -> None:
        origin = self.request.headers.get("Origin", "")
        if origin in DEV_ORIGINS:
            self.set_header("Access-Control-Allow-Origin", origin)
            self.set_header("Vary", "Origin")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")
        self.set_header("X-Content-Type-Options", "nosniff")

    def options(self) -> None:
        self.set_status(204)
        self.finish()

    def write_json(self, payload: Any, status: int | None = None) -> None:
        # Only touch the status when the caller asks for it: error paths set the
        # status first and must not have it reset to 200 here.
        if status is not None:
            self.set_status(status)
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.finish(json.dumps(payload, ensure_ascii=False))

    def json_body(self) -> dict[str, Any]:
        if not self.request.body:
            return {}
        try:
            payload = json.loads(self.request.body.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValidationError(f"body must be utf-8 encoded: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValidationError("body must be a JSON object")
        return payload

    def require_str(self, payload: dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"field {field!r} is required and must be a string")
        return value.strip()

    def optional_int(
        self,
        payload: dict[str, Any],
        field: str,
        default: int,
        *,
        low: int,
        high: int,
    ) -> int:
        value = payload.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(f"field {field!r} must be an integer")
        if not low <= value <= high:
            raise ValidationError(f"field {field!r} must be between {low} and {high}")
        return value

    def compute_etag(self) -> str | None:
        return None


__all__ = ["DEV_ORIGINS", "BaseHandler", "api_endpoint"]
