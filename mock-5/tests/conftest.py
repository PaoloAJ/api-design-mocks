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
    return {"X-Api-Key": "sk_test_alpha_demo"}


@pytest.fixture
def other_auth():
    """A second tenant, for isolation tests."""
    return {"X-Api-Key": "sk_test_beta_demo"}


@pytest.fixture
def payment(client, auth):
    response = client.post(
        "/v1/payments",
        json={
            "amount": 5000,
            "currency": "USD",
            "customer_id": "cus_test",
            "description": "Test payment",
        },
        headers=auth,
    )
    assert response.status_code == 201
    return response.get_json()


@pytest.fixture
def captured_payment(client, auth, payment):
    response = client.post(
        "/v1/payments/{}/status".format(payment["id"]),
        json={"status": "captured"},
        headers=auth,
    )
    assert response.status_code == 200
    return response.get_json()
