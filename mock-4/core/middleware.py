"""Request ID, auth, rate limiting — as before_request hooks.

Hooks rather than per-route decorators so a newly added endpoint is protected
by default. Forgetting a decorator on one route is how auth bypasses ship.
"""

import time
import uuid

from flask import g, request

from core.errors import RateLimitError, UnauthorizedError
from core.store import store

# Static keys stand in for real credentials; production validates a signed
# token or looks the key up in a secrets store. The role decides who may file
# a claim: a carrier pushes scans, it does not speak for the merchant.
API_KEYS = {
    "ship-demo-key-acme": {"merchant_id": "mer_acme", "role": "merchant"},
    "ship-demo-key-globex": {"merchant_id": "mer_globex", "role": "merchant"},
    "ship-demo-key-acme-carrier": {"merchant_id": "mer_acme", "role": "carrier"},
}

PUBLIC_PATHS = {"/health", "/"}
RATE_LIMIT = 100
RATE_WINDOW_SECONDS = 60


def register_middleware(app):
    @app.before_request
    def assign_request_id():
        """Honor an inbound trace ID or mint one.

        Echoing it back on every response, errors included, is what lets a
        merchant paste an ID into a ticket and have us find the log line.
        """
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        g.started_at = time.time()

    @app.before_request
    def authenticate():
        if request.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return None
        api_key = request.headers.get("X-Ship-Key")
        if not api_key:
            raise UnauthorizedError(
                "Missing credentials. Send an X-Ship-Key header.", code="missing_api_key"
            )
        principal = API_KEYS.get(api_key)
        if principal is None:
            # 401, not 403: the credential itself is bad. 403 would mean "we
            # know who you are, you may not do this".
            raise UnauthorizedError("Invalid API key.", code="invalid_api_key")
        g.api_key = api_key
        g.merchant_id = principal["merchant_id"]
        g.role = principal["role"]
        return None

    @app.before_request
    def enforce_rate_limit():
        """Runs after authenticate so the counter keys on a known identity.

        Keying on something spoofable (an IP, an unverified header) lets one
        caller spend another's budget, and limiting before auth means
        unauthenticated floods consume real merchants' quota.
        """
        if request.path in PUBLIC_PATHS or not hasattr(g, "api_key"):
            return None
        allowed, remaining, reset_in = store.hit_rate_limit(
            g.api_key, limit=RATE_LIMIT, window_seconds=RATE_WINDOW_SECONDS, now=time.time()
        )
        g.rate_remaining, g.rate_reset = remaining, reset_in
        if not allowed:
            raise RateLimitError(
                "Rate limit of {} requests per {}s exceeded.".format(
                    RATE_LIMIT, RATE_WINDOW_SECONDS
                ),
                retry_after=reset_in,
            )
        return None

    @app.after_request
    def attach_headers(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "unknown")
        if hasattr(g, "rate_remaining"):
            response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT)
            response.headers["X-RateLimit-Remaining"] = str(g.rate_remaining)
            response.headers["X-RateLimit-Reset"] = str(g.rate_reset)
        return response
