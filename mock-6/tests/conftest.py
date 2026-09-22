import pytest

from app import create_app
from app.core.middleware import reset_rate_limits
from app.core.store import store


@pytest.fixture
def app():
    application = create_app({"TESTING": True})
    # Store and rate-limit state are module-level singletons; isolate each test.
    store.reset()
    reset_rate_limits()
    yield application
    store.reset()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def build(client):
    response = client.post(
        "/v1/projects/web/builds",
        json={
            "branch": "main",
            "commit": "a1b2c3d4e5f6",
            "jobs": ["build", "test"],
        },
    )
    assert response.status_code == 201
    return response.get_json()
