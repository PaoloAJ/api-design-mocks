"""Cross-cutting request concerns: request IDs, auth, rate limiting, timing.

These run as `before_request`/`after_request` hooks rather than per-route
decorators so a newly added endpoint is protected by default. Forgetting a
decorator on one route is how auth bypasses ship.
"""

from __future__ import annotations

import time
import uuid

from flask import current_app, g, request

from app.core.errors import RateLimitError, UnauthorizedError
from app.core.store import store

# Static keys stand in for real credentials. A production service validates a
# signed token (JWT/OAuth) or looks the key up in a secrets store — never a
# hardcoded list. The role decides who may decide a case.
API_KEYS = {
    "mod-demo-key-alpha": {"platform_id": "plat_alpha", "role": "reviewer"},
    "mod-demo-key-beta": {"platform_id": "plat_beta", "role": "reviewer"},
    # Ingest-only credential: it may submit content but must not decide cases.
    "mod-demo-key-alpha-ingest": {"platform_id": "plat_alpha", "role": "ingest"},
}

# Paths reachable without credentials.
PUBLIC_PATHS = {"/health", "/healthz", "/", "/openapi.json"}

RATE_LIMIT = 100
RATE_WINDOW_SECONDS = 60


def register_middleware(app) -> None:
    @app.before_request
    def assign_request_id():
        """Honor an inbound trace ID, or mint one.

        Echoing this back on every response (including errors) is what lets a
        user paste an ID into a support ticket and have us find the exact log
        line.
        """
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        g.started_at = time.time()

    @app.before_request
    def authenticate():
        if request.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return None
        api_key = request.headers.get("X-Mod-Key") or _bearer_token()
        if not api_key:
            raise UnauthorizedError(
                "Missing credentials. Send an X-Mod-Key header.",
                code="missing_api_key",
            )
        principal = API_KEYS.get(api_key)
        if principal is None:
            # 401, not 403: the credential itself is bad. 403 would mean "we
            # know who you are, you may not do this".
            raise UnauthorizedError("Invalid API key.", code="invalid_api_key")
        g.api_key = api_key
        g.platform_id = principal["platform_id"]
        g.role = principal["role"]
        return None

    @app.before_request
    def enforce_rate_limit():
        """Runs after authenticate so the counter keys on a known identity.

        Keying on something spoofable (an IP, an unverified header) lets one
        caller spend another's budget, and rate limiting before auth means
        unauthenticated floods consume real tenants' quota.
        """
        if request.path in PUBLIC_PATHS or not hasattr(g, "api_key"):
            return None
        allowed, remaining, reset_in = store.hit_rate_limit(
            g.api_key,
            limit=RATE_LIMIT,
            window_seconds=RATE_WINDOW_SECONDS,
            now=time.time(),
        )
        g.rate_remaining = remaining
        g.rate_reset = reset_in
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


def _bearer_token():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :].strip()
    return None
