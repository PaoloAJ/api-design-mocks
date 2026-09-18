import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from core.store import store  # noqa: E402

ACME = {"X-Ship-Key": "ship-demo-key-acme"}
GLOBEX = {"X-Ship-Key": "ship-demo-key-globex"}
CARRIER = {"X-Ship-Key": "ship-demo-key-acme-carrier"}


@pytest.fixture
def client():
    store.reset()
    app = create_app({"TESTING": True})
    with app.test_client() as c:
        yield c
    store.reset()


@pytest.fixture
def shipment(client):
    return make_shipment(client)


def make_shipment(client, headers=ACME, **overrides):
    body = {"destination": "Portland, OR", "carrier": "ups", "weight_kg": 2.5}
    body.update(overrides)
    response = client.post("/v1/shipments", json=body, headers=headers)
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def move(client, shipment_id, *states, headers=ACME):
    """Walk a shipment through a sequence of states."""
    last = None
    for state in states:
        last = client.post(
            "/v1/shipments/{}/transition".format(shipment_id),
            json={"to_state": state},
            headers=headers,
        )
        assert last.status_code == 200, last.get_json()
    return last.get_json()
