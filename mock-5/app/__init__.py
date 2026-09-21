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

    from app.api.payments import bp as payments_bp
    from app.api.refunds import bp as refunds_bp
    from app.api.settlements import bp as settlements_bp

    app.register_blueprint(payments_bp)
    app.register_blueprint(refunds_bp)
    app.register_blueprint(settlements_bp)

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
                "payments": len(store.list("payments")),
                "refunds": len(store.list("refunds")),
            }
        )

    @app.get("/")
    def index():
        """Root discovery document: what exists and where."""
        return jsonify(
            {
                "service": "mock-5 payments API",
                "version": "v1",
                "endpoints": {
                    "payments": "/v1/payments",
                    "refunds": "/v1/refunds",
                    "settlements": "/v1/settlements",
                    "health": "/health",
                },
                "auth": "send header X-Api-Key: sk_test_alpha_demo",
            }
        )

    return app
