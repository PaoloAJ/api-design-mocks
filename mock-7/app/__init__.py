"""App factory and route registration.

Start here. The whole API is the three blueprints below plus `/healthz`, and
every request passes through `register_middleware` before reaching any of them.
"""

from __future__ import annotations

from flask import Flask, jsonify

from app.errors import register_error_handlers
from app.middleware import register_middleware


def create_app():
    app = Flask(__name__)

    # Order: middleware first so hooks are installed before any blueprint,
    # error handlers last so they wrap everything registered above.
    register_middleware(app)

    from app.events import bp as events_bp
    from app.messages import bp as messages_bp
    from app.templates import bp as templates_bp

    app.register_blueprint(messages_bp)
    app.register_blueprint(templates_bp)
    app.register_blueprint(events_bp)

    @app.get("/healthz")
    def healthz():
        """Liveness only: is this process up and serving?

        It deliberately checks nothing downstream. A health check that touches
        the datastore turns a brief blip into a restart loop, because the
        orchestrator kills the pod for a dependency's outage. There is no
        readiness counterpart yet — that gap is deliberate.
        """
        return jsonify({"status": "ok"})

    register_error_handlers(app)
    return app
