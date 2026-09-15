"""Application factory.

A factory rather than a module-level `app` so tests can build an isolated
instance per test with their own config, and so nothing binds to a port at
import time. Same pattern as mock-1.
"""

from __future__ import annotations

from flask import Flask, jsonify

from app.core.errors import register_error_handlers
from app.core.middleware import register_middleware
from app.core.store import store


def create_app(config=None):
    app = Flask(__name__)
    # Preserve our declared field order in responses. In Flask 3 this lives on
    # the JSON provider, not the old JSON_SORT_KEYS config key.
    app.json.sort_keys = False
    app.config.update(
        # 1 MiB request cap. Without this, a large body is parsed into memory
        # before any handler runs.
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
    )
    if config:
        app.config.update(config)

    register_middleware(app)
    register_error_handlers(app)

    from app.api.incidents import bp as incidents_bp
    from app.api.services import bp as services_bp

    app.register_blueprint(services_bp)
    app.register_blueprint(incidents_bp)

    @app.get("/health")
    def health():
        """Liveness probe — unauthenticated and dependency-free.

        A health check that calls the database will fail during a brief blip
        and get the container killed. Readiness (can I serve traffic?) should
        be a separate endpoint from liveness (am I alive?).
        """
        return jsonify(
            {
                "status": "ok",
                "services": len(store.list("services")),
                "incidents": len(store.list("incidents")),
            }
        )

    @app.get("/")
    def index():
        """Root discovery document: what exists and where."""
        return jsonify(
            {
                "service": "mock-2 incidents API",
                "version": "v1",
                "endpoints": {
                    "services": "/v1/services",
                    "incidents": "/v1/incidents",
                    "health": "/health",
                },
                "auth": "send header DD-API-KEY: dd-demo-key-alpha",
            }
        )

    return app
