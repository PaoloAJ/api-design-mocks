"""Entry point: `python wsgi.py` for dev, or point gunicorn at `app`."""

from app import create_app
from app.seed import seed

app = create_app()
seed()

if __name__ == "__main__":
    # Port 5001, not 5000: macOS AirPlay Receiver squats on 5000 and answers
    # with an empty 403 that looks exactly like an auth bug in this app.
    app.run(host="127.0.0.1", port=5001, debug=True)
