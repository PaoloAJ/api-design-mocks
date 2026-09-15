"""Entry point: `python wsgi.py` for dev, or point gunicorn at `app`."""

from app import create_app
from app.seed import seed

app = create_app()
seed()

if __name__ == "__main__":
    # debug=True is a dev-only convenience; it enables the interactive
    # debugger, which must never be exposed in production.
    # Port 5002: mock-1 already claims 5001, and 5000 is macOS AirPlay
    # Receiver, which replies to requests with an empty 403 that looks
    # exactly like an auth bug in your own app.
    app.run(host="127.0.0.1", port=5002, debug=True)
