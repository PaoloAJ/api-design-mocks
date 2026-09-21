"""Error types and the single JSON error shape the API returns.

Every failure — validation, not-found, conflict, rate limit, or an unhandled
exception — leaves the process through `register_error_handlers` so clients can
parse one predictable body instead of guessing between Flask's HTML 404 page
and our own JSON.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import jsonify
from werkzeug.exceptions import HTTPException


class APIError(Exception):
    """Base class for errors that map to a deliberate HTTP response.

    `code` is the stable, machine-readable string clients branch on. The HTTP
    status tells them the category (retry? fix the request?); the code tells
    them exactly what happened.
    """

    status_code = 500
    code = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        errors: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        # Field-level detail, e.g. [{"field": "amount", "message": "required"}].
        self.errors = errors or []

    def to_dict(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.errors:
            body["error"]["errors"] = self.errors
        return body


class ValidationError(APIError):
    status_code = 400
    code = "validation_error"


class NotFoundError(APIError):
    status_code = 404
    code = "not_found"


class ConflictError(APIError):
    """Used for both duplicate creates and optimistic-concurrency failures."""

    status_code = 409
    code = "conflict"


class RateLimitError(APIError):
    status_code = 429
    code = "rate_limit_exceeded"

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class UnauthorizedError(APIError):
    status_code = 401
    code = "unauthorized"


def register_error_handlers(app) -> None:
    """Funnel every error path into the same JSON envelope."""

    @app.errorhandler(APIError)
    def handle_api_error(exc: APIError):
        response = jsonify(exc.to_dict())
        response.status_code = exc.status_code
        if isinstance(exc, RateLimitError):
            # RFC 6585: tell the client how long to back off.
            response.headers["Retry-After"] = str(exc.retry_after)
        return response

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        # Catches Flask's own 404/405/415 so clients never receive HTML.
        response = jsonify(
            {
                "error": {
                    "code": exc.name.lower().replace(" ", "_"),
                    "message": exc.description,
                }
            }
        )
        response.status_code = exc.code or 500
        return response

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):  # pragma: no cover - safety net
        app.logger.exception("unhandled exception")
        response = jsonify(
            {
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                }
            }
        )
        response.status_code = 500
        return response
