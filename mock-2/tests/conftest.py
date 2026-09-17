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
def service():
    """Services are seed-only; create one directly through the store so
    tests don't depend on seed.py's fixture data."""
    return store.create(
        "services",
        {
            "name": "checkout",
            "tier": 1,
            "owner_team": "team-checkout",
            "org_id": "org_alpha",
        },
    )


@pytest.fixture
def incident(client, auth, service):
    response = client.post(
        "/v1/incidents",
        json={
            "title": "Test incident",
            "severity": "sev2",
            "service_id": service["id"],
            "summary": "Something is wrong.",
            "tags": ["env:test"],
        },
        headers=auth,
    )
    assert response.status_code == 201
    return response.get_json()
