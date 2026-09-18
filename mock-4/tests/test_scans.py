from tests.conftest import ACME, CARRIER, GLOBEX, make_shipment


def post_scans(client, scans, headers=CARRIER):
    return client.post("/v1/scans", json={"scans": scans}, headers=headers)


def test_batch_returns_202(client, shipment):
    response = post_scans(client, [{"shipment_id": shipment["id"], "code": "accepted"}])
    assert response.status_code == 202
    assert response.get_json()["received"] == 1


def test_scan_advances_shipment_state(client, shipment):
    post_scans(client, [{"shipment_id": shipment["id"], "code": "accepted"}])
    body = client.get("/v1/shipments/{}".format(shipment["id"]), headers=ACME).get_json()
    assert body["state"] == "in_transit"


def test_partial_success_reports_index_per_error(client, shipment):
    response = post_scans(
        client,
        [
            {"shipment_id": shipment["id"], "code": "accepted"},
            {"shipment_id": "shi_missing", "code": "accepted"},
            {"shipment_id": shipment["id"], "code": "not_a_code"},
        ],
    )
    body = response.get_json()
    assert response.status_code == 202
    assert len(body["accepted"]) == 1
    assert [e["index"] for e in body["errors"]] == [1, 2]


def test_scan_for_another_merchant_is_not_found(client):
    other = make_shipment(client, GLOBEX, destination="Seattle, WA")
    response = post_scans(client, [{"shipment_id": other["id"], "code": "accepted"}])
    assert response.get_json()["errors"][0]["code"] == "not_found"


def test_carrier_delivered_scan_is_authoritative(client, shipment):
    response = post_scans(client, [{"shipment_id": shipment["id"], "code": "delivered"}])
    assert response.get_json()["errors"] == []


def test_repeated_scan_of_current_state_is_accepted(client, shipment):
    post_scans(client, [{"shipment_id": shipment["id"], "code": "accepted"}])
    response = post_scans(client, [{"shipment_id": shipment["id"], "code": "departed"}])
    assert response.get_json()["errors"] == []


def test_empty_batch_is_400(client):
    response = post_scans(client, [])
    assert response.status_code == 400


def test_oversized_batch_is_400(client, shipment):
    response = post_scans(client, [{"shipment_id": shipment["id"], "code": "accepted"}] * 51)
    assert response.status_code == 400


def test_missing_scans_array_is_400(client):
    response = client.post("/v1/scans", json={"items": []}, headers=CARRIER)
    assert response.status_code == 400
