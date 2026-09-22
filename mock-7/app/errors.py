"""Error types and the single JSON shape every failure leaves through.

Validation, not-found, conflict, and unhandled exceptions all exit via
`register_error_handlers`, so a client parses one body instead of guessing
between Flask's HTML 404 page and our own JSON.
"""

from __future__ import annotations

from flask import jsonify
from werkzeug.exceptions import HTTPException


class APIError(Exception):
    """An error that maps to a deliberate HTTP response.

    `code` is the stable string clients branch on. The status says the
    category (retry? fix the request?); the code says exactly what happened.
    """

    status_code = 500
    code = "internal_error"

    def __init__(self, message, *, code=None, status_code=None, errors=None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        # Field-level detail, e.g. [{"field": "to", "message": "required"}].
        self.errors = errors or []

    def to_dict(self):
        body = {"error": {"code": self.code, "message": self.message}}
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
    """Both duplicate creates and optimistic-concurrency failures."""

    status_code = 409
    code = "conflict"


def register_error_handlers(app):
    """Funnel every error path into the same envelope."""

    @app.errorhandler(APIError)
    def handle_api_error(exc):
        response = jsonify(exc.to_dict())
        response.status_code = exc.status_code
        return response

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc):
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
    def handle_unexpected(exc):  # pragma: no cover - safety net
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
