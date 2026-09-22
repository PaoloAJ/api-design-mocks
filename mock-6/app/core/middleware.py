"""Cross-cutting request concerns: request IDs, project scope, timing.

These run as `before_request`/`after_request` hooks rather than per-route
decorators so a newly added endpoint is covered by default. Forgetting a
decorator on one route is how scoping bugs ship.

There is no authentication in this service — it sits behind an internal
gateway that terminates auth. What the gateway hands us is a project slug in
the URL, and `resolve_project` is what turns that into the scope every handler
reads from `g`.
"""

from __future__ import annotations

import time
import uuid

from flask import current_app, g, request

from app.core.errors import NotFoundError

# Stand-in for a projects table. A real service reads this from the gateway's
# token claims, which is why handlers must never take the scope from a header.
PROJECTS = {
    "web": {"id": "prj_web", "slug": "web", "name": "web-frontend"},
    "api": {"id": "prj_api", "slug": "api", "name": "api-backend"},
}

RATE_LIMIT = 120
RATE_WINDOW_SECONDS = 60

# Fixed-window counters: client ip -> (window_start, count).
_rate = {}


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
    def resolve_project():
        """Turn the `/v1/projects/<slug>` prefix into a scope on `g`.

        Runs before rate limiting so the counter keys on a resolved request,
        and before any handler so no handler has to re-derive the scope.
        """
        g.project = None
        parts = request.path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "projects":
            project = PROJECTS.get(parts[2])
            if project is None:
                # 404 on the collection root itself: an unknown project is
                # indistinguishable from one the caller may not see.
                raise NotFoundError(
                    "Project '{}' was not found.".format(parts[2]),
                    code="project_not_found",
                )
            g.project = project

    @app.before_request
    def enforce_rate_limit():
        """Fixed-window limiter keyed by client IP.

        Cheap, but it allows a burst of up to 2x the limit across a window
        boundary. A sliding window or token bucket fixes that at the cost of
        more state per key.
        """
        if request.path == "/healthz":
            return None
        now = time.time()
        key = request.remote_addr or "unknown"
        window_start, count = _rate.get(key, (now, 0))
        if now - window_start >= RATE_WINDOW_SECONDS:
            window_start, count = now, 0
        count += 1
        _rate[key] = (window_start, count)
        g.rate_remaining = max(0, RATE_LIMIT - count)
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
