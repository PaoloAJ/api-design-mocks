from tests.conftest import ACME, CARRIER, GLOBEX, make_shipment, move


def test_create_returns_201_location_and_etag(client):
    response = client.post(
        "/v1/shipments",
        json={"destination": "Austin, TX", "carrier": "fedex", "weight_kg": 3.0},
        headers=ACME,
    )
    assert response.status_code == 201
    assert response.headers["Location"].startswith("/v1/shipments/shi_")
    assert response.headers["ETag"] == '"1"'
    assert response.get_json()["state"] == "label_created"


def test_create_collects_all_field_errors(client):
    response = client.post(
        "/v1/shipments", json={"carrier": "rocket", "weight_kg": -1}, headers=ACME
    )
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["error"]["errors"]}
    assert fields == {"destination", "carrier", "weight_kg"}


def test_create_rejects_state_in_body(client):
    response = client.post(
        "/v1/shipments",
        json={"destination": "X", "carrier": "ups", "weight_kg": 1, "state": "delivered"},
        headers=ACME,
    )
    assert response.status_code == 400
    assert any(e["field"] == "state" for e in response.get_json()["error"]["errors"])


def test_create_rejects_overweight_parcel(client):
    response = client.post(
        "/v1/shipments", json={"destination": "X", "carrier": "ups", "weight_kg": 90}, headers=ACME
    )
    assert response.status_code == 400


def test_response_never_leaks_merchant_id(client, shipment):
    assert "merchant_id" not in shipment
    listed = client.get("/v1/shipments", headers=ACME).get_json()["data"][0]
    assert "merchant_id" not in listed


def test_get_other_tenants_shipment_is_404_not_403(client, shipment):
    response = client.get("/v1/shipments/{}".format(shipment["id"]), headers=GLOBEX)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_list_is_scoped_to_caller(client):
    make_shipment(client, ACME)
    make_shipment(client, GLOBEX, destination="Seattle, WA")
    acme = client.get("/v1/shipments", headers=ACME).get_json()["data"]
    globex = client.get("/v1/shipments", headers=GLOBEX).get_json()["data"]
    assert len(acme) == 1 and len(globex) == 1
    assert acme[0]["id"] != globex[0]["id"]


def test_patch_updates_mutable_fields(client, shipment):
    response = client.patch(
        "/v1/shipments/{}".format(shipment["id"]),
        json={"destination": "Boise, ID"},
        headers=ACME,
    )
    assert response.status_code == 200
    assert response.get_json()["destination"] == "Boise, ID"
    assert response.get_json()["version"] == 2


def test_patch_rejects_state_writes(client, shipment):
    response = client.patch(
        "/v1/shipments/{}".format(shipment["id"]), json={"state": "delivered"}, headers=ACME
    )
    assert response.status_code == 400


def test_patch_with_no_editable_fields_is_400(client, shipment):
    response = client.patch("/v1/shipments/{}".format(shipment["id"]), json={}, headers=ACME)
    assert response.status_code == 400


def test_if_match_stale_version_is_409(client, shipment):
    path = "/v1/shipments/{}".format(shipment["id"])
    client.patch(path, json={"destination": "First"}, headers=ACME)
    response = client.patch(
        path, json={"destination": "Second"}, headers={**ACME, "If-Match": '"1"'}
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "version_conflict"


def test_if_match_current_version_succeeds(client, shipment):
    response = client.patch(
        "/v1/shipments/{}".format(shipment["id"]),
        json={"destination": "Boise, ID"},
        headers={**ACME, "If-Match": '"1"'},
    )
    assert response.status_code == 200


def test_malformed_if_match_is_400(client, shipment):
    response = client.patch(
        "/v1/shipments/{}".format(shipment["id"]),
        json={"destination": "X"},
        headers={**ACME, "If-Match": "not-a-version"},
    )
    assert response.status_code == 400


def test_idempotent_post_returns_original(client):
    body = {"destination": "Austin, TX", "carrier": "ups", "weight_kg": 1.0}
    headers = {**ACME, "Idempotency-Key": "abc-123"}
    first = client.post("/v1/shipments", json=body, headers=headers)
    second = client.post("/v1/shipments", json=body, headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert first.get_json()["id"] == second.get_json()["id"]


def test_claim_requires_carrier_custody(client, shipment):
    response = client.post(
        "/v1/shipments/{}/claims".format(shipment["id"]), json={"reason": "lost"}, headers=ACME
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "not_claimable"


def test_claim_succeeds_once_in_transit(client, shipment):
    move(client, shipment["id"], "in_transit")
    response = client.post(
        "/v1/shipments/{}/claims".format(shipment["id"]), json={"reason": "damaged"}, headers=ACME
    )
    assert response.status_code == 201
    assert response.get_json()["status"] == "open"


def test_second_open_claim_is_409(client, shipment):
    move(client, shipment["id"], "in_transit")
    path = "/v1/shipments/{}/claims".format(shipment["id"])
    client.post(path, json={"reason": "lost"}, headers=ACME)
    response = client.post(path, json={"reason": "damaged"}, headers=ACME)
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "duplicate_claim"


def test_carrier_role_may_not_file_a_claim(client, shipment):
    move(client, shipment["id"], "in_transit")
    response = client.post(
        "/v1/shipments/{}/claims".format(shipment["id"]), json={"reason": "lost"}, headers=CARRIER
    )
    assert response.status_code == 403


def test_claim_rejects_unknown_reason(client, shipment):
    move(client, shipment["id"], "in_transit")
    response = client.post(
        "/v1/shipments/{}/claims".format(shipment["id"]), json={"reason": "vibes"}, headers=ACME
    )
    assert response.status_code == 400
