import pytest

from app import create_app
from app.core.store import store


@pytest.fixture
def app():
    application = create_app({"TESTING": True})
    store.reset()  # Store is a module-level singleton; isolate each test.
    yield application
    store.reset()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth():
    return {"X-Mod-Key": "mod-demo-key-alpha"}


@pytest.fixture
def other_auth():
    """A second tenant, for isolation tests."""
    return {"X-Mod-Key": "mod-demo-key-beta"}


@pytest.fixture
def ingest_auth():
    """Same tenant as `auth`, but may not decide cases."""
    return {"X-Mod-Key": "mod-demo-key-alpha-ingest"}


@pytest.fixture
def case(client, auth):
    response = client.post(
        "/v1/cases",
        json={
            "external_id": "post_1",
            "kind": "post",
            "text": "Reported content under review",
            "reason": "spam",
            "labels": ["lang:en"],
        },
        headers=auth,
    )
    assert response.status_code == 201
    return response.get_json()
