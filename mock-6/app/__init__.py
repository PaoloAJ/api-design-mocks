"""Application factory.

A factory rather than a module-level `app` so tests can build an isolated
instance with their own config, and so nothing binds to a port at import time.
"""

from __future__ import annotations

from flask import Flask, jsonify

from app.core.errors import register_error_handlers
from app.core.middleware import register_middleware
from app.core.store import store


def create_app(config=None):
    app = Flask(__name__)
    # Keep our declared field order in responses. In Flask 3 this lives on the
    # JSON provider, not the old JSON_SORT_KEYS config key.
    app.json.sort_keys = False
    # 1 MiB cap. Without it, a large body is parsed into memory before any
    # handler runs.
    app.config.update(MAX_CONTENT_LENGTH=1 * 1024 * 1024)
    if config:
        app.config.update(config)

    register_middleware(app)
    register_error_handlers(app)

    from app.api.builds import bp as builds_bp
    from app.api.events import bp as events_bp
    from app.api.jobs import bp as jobs_bp

    app.register_blueprint(builds_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(events_bp)

    @app.get("/healthz")
    def healthz():
        """Liveness probe — dependency-free on purpose.

        A health check that calls the datastore fails during a brief blip and
        gets the container killed. Readiness (can I serve traffic?) belongs in
        a separate endpoint from liveness (am I alive?); there isn't one here.
        """
        return jsonify({"status": "ok", "builds": len(store.list("builds"))})

    @app.get("/")
    def index():
        """Root discovery document: what exists and where."""
        return jsonify(
            {
                "service": "mock-5 builds API",
                "version": "v1",
                "projects": ["web", "api"],
                "endpoints": {
                    "builds": "/v1/projects/{slug}/builds",
                    "jobs": "/v1/projects/{slug}/builds/{id}/jobs",
                    "events": "/v1/events",
                    "health": "/healthz",
                },
            }
        )

    return app
