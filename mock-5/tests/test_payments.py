"""Contract tests for /v1/payments."""


def test_health_needs_no_auth(client):
    assert client.get("/health").status_code == 200


def test_missing_api_key_is_401(client):
    response = client.get("/v1/payments")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "missing_api_key"


def test_bad_api_key_is_401(client):
    response = client.get("/v1/payments", headers={"X-Api-Key": "nope"})
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_api_key"


def test_create_returns_201_with_location_and_etag(client, auth):
    response = client.post(
        "/v1/payments",
        json={"amount": 2500, "currency": "USD", "customer_id": "cus_1"},
        headers=auth,
    )
    assert response.status_code == 201
    assert response.headers["Location"].startswith("/v1/payments/pay_")
    assert response.headers["ETag"].startswith('W/"pay_')
    body = response.get_json()
    assert body["status"] == "authorized"
    assert body["amount_refunded"] == 0
    assert body["refundable_amount"] == 2500
    assert body["version"] == 1


def test_create_collects_all_validation_errors(client, auth):
    response = client.post("/v1/payments", json={"amount": "oops"}, headers=auth)
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["error"]["errors"]}
    assert {"amount", "currency", "customer_id"} <= fields


def test_amount_must_be_a_positive_integer(client, auth):
    response = client.post(
        "/v1/payments",
        json={"amount": 0, "currency": "USD", "customer_id": "cus_1"},
        headers=auth,
    )
    assert response.status_code == 400

    response = client.post(
        "/v1/payments",
        json={"amount": 19.99, "currency": "USD", "customer_id": "cus_1"},
        headers=auth,
    )
    assert response.status_code == 400


def test_unsupported_currency_is_400(client, auth):
    response = client.post(
        "/v1/payments",
        json={"amount": 100, "currency": "JPY", "customer_id": "cus_1"},
        headers=auth,
    )
    assert response.status_code == 400


def test_unknown_field_is_rejected(client, auth):
    response = client.post(
        "/v1/payments",
        json={"amount": 100, "currency": "USD", "customer_id": "cus_1", "amonut": 1},
        headers=auth,
    )
    assert response.status_code == 400
    assert any(
        e["field"] == "amonut" for e in response.get_json()["error"]["errors"]
    )


def test_idempotency_key_replays_instead_of_duplicating(client, auth):
    payload = {"amount": 100, "currency": "USD", "customer_id": "cus_1"}
    headers = dict(auth)
    headers["Idempotency-Key"] = "abc-123"

    first = client.post("/v1/payments", json=payload, headers=headers)
    second = client.post("/v1/payments", json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"
    assert first.get_json()["id"] == second.get_json()["id"]


def test_get_missing_payment_is_404_json(client, auth):
    response = client.get("/v1/payments/pay_doesnotexist", headers=auth)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_if_none_match_returns_304(client, auth, payment):
    etag = client.get(
        "/v1/payments/{}".format(payment["id"]), headers=auth
    ).headers["ETag"]
    headers = dict(auth)
    headers["If-None-Match"] = etag
    response = client.get("/v1/payments/{}".format(payment["id"]), headers=headers)
    assert response.status_code == 304


def test_patch_updates_only_supplied_fields(client, auth, payment):
    response = client.patch(
        "/v1/payments/{}".format(payment["id"]),
        json={"description": "Updated label"},
        headers=auth,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["description"] == "Updated label"
    assert body["amount"] == payment["amount"]
    assert body["version"] == payment["version"] + 1


def test_patch_cannot_touch_amount_or_currency(client, auth, payment):
    response = client.patch(
        "/v1/payments/{}".format(payment["id"]),
        json={"amount": 1},
        headers=auth,
    )
    assert response.status_code == 400


def test_patch_with_empty_body_is_400(client, auth, payment):
    response = client.patch(
        "/v1/payments/{}".format(payment["id"]), json={}, headers=auth
    )
    assert response.status_code == 400


def test_stale_if_match_is_409(client, auth, payment):
    headers = dict(auth)
    headers["If-Match"] = 'W/"{}-1"'.format(payment["id"])

    first = client.patch(
        "/v1/payments/{}".format(payment["id"]),
        json={"description": "First edit"},
        headers=headers,
    )
    assert first.status_code == 200

    # Same stale version replayed: the resource is now at version 2.
    second = client.patch(
        "/v1/payments/{}".format(payment["id"]),
        json={"description": "Second edit"},
        headers=headers,
    )
    assert second.status_code == 409
    assert second.get_json()["error"]["code"] == "version_conflict"


def test_no_delete_endpoint(client, auth, payment):
    """Payments cannot be deleted — void or refund leave an audit trail."""
    response = client.delete("/v1/payments/{}".format(payment["id"]), headers=auth)
    assert response.status_code == 405


def test_tenant_isolation_hides_other_org(client, auth, other_auth, payment):
    response = client.get("/v1/payments/{}".format(payment["id"]), headers=other_auth)
    assert response.status_code == 404  # 404 not 403, to avoid leaking existence


def test_request_id_is_echoed(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-me"})
    assert response.headers["X-Request-ID"] == "trace-me"


def test_method_not_allowed_is_json(client, auth):
    response = client.put("/v1/payments", json={}, headers=auth)
    assert response.status_code == 405
    assert response.get_json()["error"]["code"]
