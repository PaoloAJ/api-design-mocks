"""Application factory.

A factory rather than a module-level `app` so tests build an isolated instance
with their own config, and nothing binds to a port at import time.
"""

from flask import Flask, jsonify

from core.errors import register_error_handlers
from core.middleware import register_middleware
from core.store import store


def create_app(config=None):
    app = Flask(__name__)
    app.json.sort_keys = False  # preserve declared field order
    # 1 MiB cap: without it a large body is parsed into memory before any
    # handler runs.
    app.config.update(MAX_CONTENT_LENGTH=1 * 1024 * 1024)
    if config:
        app.config.update(config)

    register_middleware(app)
    register_error_handlers(app)

    from api.scans import bp as scans_bp
    from api.shipments import bp as shipments_bp

    app.register_blueprint(shipments_bp)
    app.register_blueprint(scans_bp)

    @app.get("/health")
    def health():
        """Liveness probe — unauthenticated and dependency-free.

        A health check that calls the database fails during a brief blip and
        gets the container killed. Readiness (can I serve traffic?) belongs on
        a separate endpoint from liveness (am I alive?) — there isn't one here.
        """
        return jsonify({"status": "ok", "shipments": len(store.list("shipments"))})

    @app.get("/")
    def index():
        """Root discovery document: what exists and where."""
        return jsonify(
            {
                "service": "mock-4 shipping API",
                "version": "v1",
                "endpoints": {
                    "shipments": "/v1/shipments",
                    "scans": "/v1/scans",
                    "health": "/health",
                },
                "auth": "send header X-Ship-Key: ship-demo-key-acme",
            }
        )

    return app
