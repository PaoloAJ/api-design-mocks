"""Error types and the one JSON shape every failure leaves through.

Framework 404s and unhandled exceptions funnel through here too, so a client
parses one predictable body instead of guessing between Flask's HTML error
page and ours.
"""

from flask import jsonify
from werkzeug.exceptions import HTTPException


class APIError(Exception):
    """`code` is the stable string clients branch on; the status is the
    category (retry? fix the request?), the code is which thing went wrong."""

    status_code, code = 500, "internal_error"

    def __init__(self, message, *, code=None, status_code=None, errors=None):
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        # Field detail: [{"field": "weight_kg", "message": "required"}]
        self.errors = errors or []

    def to_dict(self):
        body = {"error": {"code": self.code, "message": self.message}}
        if self.errors:
            body["error"]["errors"] = self.errors
        return body


class ValidationError(APIError):
    status_code, code = 400, "validation_error"


class UnauthorizedError(APIError):
    status_code, code = 401, "unauthorized"


class ForbiddenError(APIError):
    """Known caller, disallowed action — unlike the 404 we give for another
    merchant's shipment, where confirming the ID exists would itself leak."""

    status_code, code = 403, "forbidden"


class NotFoundError(APIError):
    status_code, code = 404, "not_found"


class ConflictError(APIError):
    """Illegal state transitions and If-Match version conflicts alike."""

    status_code, code = 409, "conflict"


class RateLimitError(APIError):
    status_code, code = 429, "rate_limit_exceeded"

    def __init__(self, message, *, retry_after):
        super().__init__(message)
        self.retry_after = retry_after


def register_error_handlers(app):
    @app.errorhandler(APIError)
    def handle_api_error(exc):
        response = jsonify(exc.to_dict())
        response.status_code = exc.status_code
        if isinstance(exc, RateLimitError):
            response.headers["Retry-After"] = str(exc.retry_after)  # RFC 6585
        return response

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc):
        # Catches Flask's own 404/405/415 so clients never receive HTML.
        body = {"error": {"code": exc.name.lower().replace(" ", "_"), "message": exc.description}}
        response = jsonify(body)
        response.status_code = exc.code or 500
        return response

    @app.errorhandler(Exception)
    def handle_unexpected(exc):  # pragma: no cover - safety net
        app.logger.exception("unhandled exception")
        response = jsonify(
            {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}
        )
        response.status_code = 500
        return response
