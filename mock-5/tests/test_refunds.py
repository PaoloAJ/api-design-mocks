"""Refunds: the human action that ends a payment's story, and its balance rule."""


def test_refund_requires_captured_or_settled(client, auth, payment):
    response = client.post(
        "/v1/payments/{}/refund".format(payment["id"]), json={}, headers=auth
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "invalid_state_transition"


def test_full_refund_defaults_to_remaining_balance(client, auth, captured_payment):
    response = client.post(
        "/v1/payments/{}/refund".format(captured_payment["id"]), json={}, headers=auth
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["amount"] == captured_payment["amount"]

    payment_after = client.get(
        "/v1/payments/{}".format(captured_payment["id"]), headers=auth
    ).get_json()
    assert payment_after["amount_refunded"] == captured_payment["amount"]
    assert payment_after["refundable_amount"] == 0


def test_partial_refund_reduces_refundable_amount(client, auth, captured_payment):
    response = client.post(
        "/v1/payments/{}/refund".format(captured_payment["id"]),
        json={"amount": 1000},
        headers=auth,
    )
    assert response.status_code == 201

    payment_after = client.get(
        "/v1/payments/{}".format(captured_payment["id"]), headers=auth
    ).get_json()
    assert payment_after["amount_refunded"] == 1000
    assert payment_after["refundable_amount"] == captured_payment["amount"] - 1000


def test_two_partial_refunds_cannot_exceed_the_balance(client, auth, captured_payment):
    remaining = captured_payment["amount"]
    first = client.post(
        "/v1/payments/{}/refund".format(captured_payment["id"]),
        json={"amount": remaining - 100},
        headers=auth,
    )
    assert first.status_code == 201

    # Only 100 left refundable; asking for 200 must fail even though 200 is
    # comfortably under the *original* payment amount.
    second = client.post(
        "/v1/payments/{}/refund".format(captured_payment["id"]),
        json={"amount": 200},
        headers=auth,
    )
    assert second.status_code == 400
    fields = {e["field"] for e in second.get_json()["error"]["errors"]}
    assert "amount" in fields


def test_refund_amount_must_be_positive(client, auth, captured_payment):
    response = client.post(
        "/v1/payments/{}/refund".format(captured_payment["id"]),
        json={"amount": 0},
        headers=auth,
    )
    assert response.status_code == 400


def test_refund_appears_in_nested_and_top_level_list(client, auth, captured_payment):
    client.post(
        "/v1/payments/{}/refund".format(captured_payment["id"]),
        json={"amount": 500},
        headers=auth,
    )
    nested = client.get(
        "/v1/payments/{}/refunds".format(captured_payment["id"]), headers=auth
    ).get_json()
    assert len(nested["data"]) == 1

    top_level = client.get("/v1/refunds", headers=auth).get_json()
    assert len(top_level["data"]) == 1
    assert top_level["data"][0]["payment_id"] == captured_payment["id"]


def test_get_refund_by_id(client, auth, captured_payment):
    created = client.post(
        "/v1/payments/{}/refund".format(captured_payment["id"]),
        json={"amount": 500, "reason": "duplicate charge"},
        headers=auth,
    ).get_json()
    fetched = client.get("/v1/refunds/{}".format(created["id"]), headers=auth).get_json()
    assert fetched["reason"] == "duplicate charge"


def test_refund_tenant_isolation(client, auth, other_auth, captured_payment):
    created = client.post(
        "/v1/payments/{}/refund".format(captured_payment["id"]),
        json={"amount": 500},
        headers=auth,
    ).get_json()
    response = client.get("/v1/refunds/{}".format(created["id"]), headers=other_auth)
    assert response.status_code == 404
