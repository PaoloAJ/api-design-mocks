"""Auth, error envelope, pagination, filtering — the cross-cutting contract."""

from tests.conftest import ACME, GLOBEX, make_shipment


def test_missing_key_is_401(client):
    response = client.get("/v1/shipments")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "missing_api_key"


def test_bad_key_is_401_not_403(client):
    response = client.get("/v1/shipments", headers={"X-Ship-Key": "nope"})
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_api_key"


def test_health_is_public(client):
    assert client.get("/health").status_code == 200


def test_root_is_public(client):
    assert client.get("/").status_code == 200


def test_request_id_is_echoed(client):
    response = client.get("/v1/shipments", headers={**ACME, "X-Request-ID": "trace-42"})
    assert response.headers["X-Request-ID"] == "trace-42"


def test_request_id_minted_when_absent(client):
    assert client.get("/v1/shipments", headers=ACME).headers["X-Request-ID"]


def test_rate_limit_headers_present(client):
    response = client.get("/v1/shipments", headers=ACME)
    assert response.headers["X-RateLimit-Limit"] == "100"
    assert int(response.headers["X-RateLimit-Remaining"]) < 100


def test_unknown_route_returns_json_envelope(client):
    response = client.get("/v1/nope", headers=ACME)
    assert response.status_code == 404
    assert "error" in response.get_json()


def test_wrong_method_returns_json_envelope(client):
    response = client.delete("/v1/shipments", headers=ACME)
    assert response.status_code == 405
    assert "error" in response.get_json()


def test_filter_by_state(client):
    kept = make_shipment(client, ACME)
    make_shipment(client, ACME, destination="Austin, TX")
    client.post(
        "/v1/shipments/{}/transition".format(kept["id"]),
        json={"to_state": "in_transit"},
        headers=ACME,
    )
    data = client.get("/v1/shipments?state=in_transit", headers=ACME).get_json()["data"]
    assert [s["id"] for s in data] == [kept["id"]]


def test_filter_by_carrier(client):
    make_shipment(client, ACME, carrier="ups")
    make_shipment(client, ACME, carrier="dhl", destination="Miami, FL")
    data = client.get("/v1/shipments?carrier=dhl", headers=ACME).get_json()["data"]
    assert len(data) == 1 and data[0]["carrier"] == "dhl"


def test_unknown_state_filter_is_400(client):
    assert client.get("/v1/shipments?state=floating", headers=ACME).status_code == 400


def test_filtering_happens_before_pagination(client):
    for index in range(6):
        made = make_shipment(client, ACME, destination="Stop {}".format(index))
        if index % 2 == 0:
            client.post(
                "/v1/shipments/{}/transition".format(made["id"]),
                json={"to_state": "cancelled"},
                headers=ACME,
            )
    body = client.get("/v1/shipments?state=cancelled&limit=2", headers=ACME).get_json()
    # Three cancelled overall: a full page of 2 and more to come. Filtering
    # after slicing would yield a short page here.
    assert len(body["data"]) == 2
    assert body["has_more"] is True


def test_cursor_walks_every_record_once(client):
    for index in range(7):
        make_shipment(client, ACME, destination="Stop {}".format(index))
    seen, cursor = [], None
    for _ in range(10):
        url = "/v1/shipments?limit=3" + ("&cursor={}".format(cursor) if cursor else "")
        body = client.get(url, headers=ACME).get_json()
        seen.extend(s["id"] for s in body["data"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 7 and len(set(seen)) == 7


def test_last_page_has_no_cursor(client):
    make_shipment(client, ACME)
    body = client.get("/v1/shipments?limit=25", headers=ACME).get_json()
    assert body["next_cursor"] is None and body["has_more"] is False


def test_malformed_cursor_is_400(client):
    response = client.get("/v1/shipments?cursor=!!!not-base64!!!", headers=ACME)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"


def test_limit_is_clamped_not_rejected(client):
    make_shipment(client, ACME)
    assert client.get("/v1/shipments?limit=5000", headers=ACME).status_code == 200


def test_non_integer_limit_is_400(client):
    assert client.get("/v1/shipments?limit=many", headers=ACME).status_code == 400


def test_zero_limit_is_400(client):
    assert client.get("/v1/shipments?limit=0", headers=ACME).status_code == 400


def test_pagination_does_not_cross_tenants(client):
    for index in range(3):
        make_shipment(client, ACME, destination="A{}".format(index))
    make_shipment(client, GLOBEX, destination="G0")
    body = client.get("/v1/shipments?limit=10", headers=GLOBEX).get_json()
    assert len(body["data"]) == 1
