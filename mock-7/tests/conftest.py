from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.middleware import reset_rate_limits  # noqa: E402
from app.store import store  # noqa: E402
from seed import ACME, GLOBEX, seed  # noqa: E402

ACME_AUTH = {"Authorization": "Bearer key_acme"}
GLOBEX_AUTH = {"Authorization": "Bearer key_globex"}


@pytest.fixture
def app():
    application = create_app()
    application.config.update(TESTING=True)
    store.reset()
    reset_rate_limits()
    seed()
    yield application
    store.reset()
    reset_rate_limits()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def template_id(client):
    body = client.get("/v1/templates", headers=ACME_AUTH).get_json()
    return body["data"][0]["id"]


def send(client, template_id, **overrides):
    payload = {
        "to": "someone@example.com",
        "subject": "Hello",
        "template_id": template_id,
    }
    payload.update(overrides)
    headers = dict(ACME_AUTH)
    headers.update(overrides.pop("headers", {}))
    return client.post("/v1/messages", json=payload, headers=headers)
