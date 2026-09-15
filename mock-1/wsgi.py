"""Entry point: `python wsgi.py` for dev, or point gunicorn at `app`."""

from app import create_app
from app.seed import seed

app = create_app()
seed()

if __name__ == "__main__":
    # debug=True is a dev-only convenience; it enables the interactive
    # debugger, which must never be exposed in production.
    # Port 5001, not 5000: macOS AirPlay Receiver squats on 5000 and will
    # answer requests with an empty 403 that looks like an app bug.
    app.run(host="127.0.0.1", port=5001, debug=True)
