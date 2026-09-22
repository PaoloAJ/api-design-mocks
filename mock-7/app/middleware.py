"""Cross-cutting request concerns: request IDs, auth, rate limiting, timing.

These run as `before_request`/`after_request` hooks rather than per-route
decorators so a newly added endpoint is covered by default. Forgetting a
decorator on one route is how tenant-scoping bugs ship.

Order matters and is deliberate:

    request_id -> authenticate -> rate_limit -> handler

Auth runs before rate limiting so the counter keys on a known account rather
than an IP. Keying on IP would let one customer behind a shared NAT exhaust
another's budget, and would let an attacker rotate IPs to get a fresh one.
"""

from __future__ import annotations

import time
import uuid

from flask import current_app, g, request

from app.errors import APIError

# Stand-in for an accounts table. A real service looks the key up in a store
# and compares a hash; this is a dict so the auth path stays readable.
API_KEYS = {
    "key_acme": {"id": "acct_acme", "name": "Acme Corp"},
    "key_globex": {"id": "acct_globex", "name": "Globex"},
}

# Endpoints that must answer before the caller has proven anything.
PUBLIC_PATHS = {"/healthz"}

RATE_LIMIT = 120
RATE_WINDOW_SECONDS = 60

# Fixed-window counters: account id -> (window_start, count).
_rate = {}


class UnauthorizedError(APIError):
    status_code = 401
    code = "unauthorized"


class RateLimitedError(APIError):
    status_code = 429
    code = "rate_limited"


def register_middleware(app):
    @app.before_request
    def assign_request_id():
        """Honor an inbound trace ID, or mint one.

        Echoing this back on every response, errors included, is what lets a
        user paste an ID into a ticket and have us find the exact log line.
        """
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        g.started_at = time.time()

    @app.before_request
    def authenticate():
        """Resolve the API key into the account every handler scopes to.

        Handlers read `g.account`, never a header or a body field. That is the
        whole tenant-isolation story: one place decides who you are.
        """
        g.account = None
        if request.path in PUBLIC_PATHS:
            return None
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise UnauthorizedError(
                "Missing or malformed Authorization header. "
                "Expected `Authorization: Bearer <api-key>`."
            )
        account = API_KEYS.get(header[len("Bearer ") :].strip())
        if account is None:
            raise UnauthorizedError("Unknown API key.")
        g.account = account
        return None

    @app.before_request
    def enforce_rate_limit():
        """Fixed-window limiter keyed by the authenticated account.

        Cheap, but it allows a burst of up to 2x the limit across a window
        boundary. A sliding window or token bucket fixes that at the cost of
        more state per key.
        """
        if g.account is None:
            return None
        now = time.time()
        key = g.account["id"]
        window_start, count = _rate.get(key, (now, 0))
        if now - window_start >= RATE_WINDOW_SECONDS:
            window_start, count = now, 0
        count += 1
        _rate[key] = (window_start, count)
        g.rate_remaining = max(0, RATE_LIMIT - count)
        if count > RATE_LIMIT:
            raise RateLimitedError("Rate limit exceeded. Retry in a minute.")
        return None

    @app.after_request
    def attach_headers(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "unknown")
        if hasattr(g, "rate_remaining"):
            response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT)
            response.headers["X-RateLimit-Remaining"] = str(g.rate_remaining)
        if hasattr(g, "started_at"):
            elapsed_ms = (time.time() - g.started_at) * 1000
            response.headers["X-Response-Time-Ms"] = "{:.2f}".format(elapsed_ms)
            current_app.logger.info(
                "%s %s -> %s in %.2fms (request_id=%s)",
                request.method,
                request.path,
                response.status_code,
                elapsed_ms,
                getattr(g, "request_id", "-"),
            )
        return response


def reset_rate_limits():
    """Tests share a process; the window state has to be clearable."""
    _rate.clear()
