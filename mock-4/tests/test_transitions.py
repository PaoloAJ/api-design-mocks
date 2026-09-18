from tests.conftest import ACME, move


def test_label_created_to_in_transit(client, shipment):
    body = move(client, shipment["id"], "in_transit")
    assert body["state"] == "in_transit"


def test_full_happy_path_stamps_delivered_at(client, shipment):
    body = move(client, shipment["id"], "in_transit", "out_for_delivery", "delivered")
    assert body["state"] == "delivered"
    assert body["delivered_at"] is not None


def test_delivered_is_terminal(client, shipment):
    move(client, shipment["id"], "in_transit", "out_for_delivery", "delivered")
    response = client.post(
        "/v1/shipments/{}/transition".format(shipment["id"]),
        json={"to_state": "in_transit"},
        headers=ACME,
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "illegal_transition"


def test_cannot_skip_from_label_created_to_delivered(client, shipment):
    response = client.post(
        "/v1/shipments/{}/transition".format(shipment["id"]),
        json={"to_state": "delivered"},
        headers=ACME,
    )
    assert response.status_code == 409


def test_exception_can_recover_to_in_transit(client, shipment):
    body = move(client, shipment["id"], "in_transit", "exception", "in_transit")
    assert body["state"] == "in_transit"


def test_cancel_records_who_cancelled(client, shipment):
    body = move(client, shipment["id"], "cancelled")
    assert body["state"] == "cancelled"


def test_cancelled_is_terminal(client, shipment):
    move(client, shipment["id"], "cancelled")
    response = client.post(
        "/v1/shipments/{}/transition".format(shipment["id"]),
        json={"to_state": "in_transit"},
        headers=ACME,
    )
    assert response.status_code == 409


def test_unknown_state_is_400_not_409(client, shipment):
    response = client.post(
        "/v1/shipments/{}/transition".format(shipment["id"]),
        json={"to_state": "teleported"},
        headers=ACME,
    )
    assert response.status_code == 400


def test_missing_to_state_is_400(client, shipment):
    response = client.post(
        "/v1/shipments/{}/transition".format(shipment["id"]), json={}, headers=ACME
    )
    assert response.status_code == 400
