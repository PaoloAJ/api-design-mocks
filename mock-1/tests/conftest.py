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
    return {"DD-API-KEY": "dd-demo-key-alpha"}


@pytest.fixture
def other_auth():
    """A second tenant, for isolation tests."""
    return {"DD-API-KEY": "dd-demo-key-beta"}


@pytest.fixture
def monitor(client, auth):
    response = client.post(
        "/v1/monitors",
        json={
            "name": "Test monitor",
            "type": "metric",
            "query": "avg(last_5m):avg:cpu.user{*} > 90",
            "thresholds": {"warning": 70, "critical": 90},
            "tags": ["env:test"],
        },
        headers=auth,
    )
    assert response.status_code == 201
    return response.get_json()
