"""Application factory.

A factory rather than a module-level `app` so tests can build an isolated
instance per test with their own config, and so nothing binds to a port at
import time.
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

    from app.api.alerts import bp as alerts_bp
    from app.api.metrics import bp as metrics_bp
    from app.api.monitors import bp as monitors_bp

    app.register_blueprint(monitors_bp)
    app.register_blueprint(alerts_bp)
    app.register_blueprint(metrics_bp)

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
                "monitors": len(store.list("monitors")),
                "alerts": len(store.list("alerts")),
            }
        )

    @app.get("/")
    def index():
        """Root discovery document: what exists and where."""
        return jsonify(
            {
                "service": "mock-1 monitors API",
                "version": "v1",
                "endpoints": {
                    "monitors": "/v1/monitors",
                    "alerts": "/v1/alerts",
                    "series": "/v1/series",
                    "health": "/health",
                },
                "auth": "send header DD-API-KEY: dd-demo-key-alpha",
            }
        )

    return app
