"""Bulk settlement ingest: the async boundary, 202, and partial success."""


def test_valid_batch_returns_202_and_settles_the_payment(client, auth, captured_payment):
    response = client.post(
        "/v1/settlements",
        json={
            "settlements": [
                {
                    "payment_id": captured_payment["id"],
                    "outcome": "settled",
                    "reference": "batch_1",
                }
            ]
        },
        headers=auth,
    )
    assert response.status_code == 202
    assert response.get_json() == {"accepted": 1, "rejected": 0, "errors": []}

    updated = client.get(
        "/v1/payments/{}".format(captured_payment["id"]), headers=auth
    ).get_json()
    assert updated["status"] == "settled"
    assert updated["settlement_reference"] == "batch_1"


def test_failed_outcome_marks_payment_failed(client, auth, captured_payment):
    response = client.post(
        "/v1/settlements",
        json={"settlements": [{"payment_id": captured_payment["id"], "outcome": "failed"}]},
        headers=auth,
    )
    assert response.status_code == 202
    updated = client.get(
        "/v1/payments/{}".format(captured_payment["id"]), headers=auth
    ).get_json()
    assert updated["status"] == "failed"


def test_partial_success_reports_failed_indexes(client, auth, captured_payment):
    response = client.post(
        "/v1/settlements",
        json={
            "settlements": [
                {"payment_id": captured_payment["id"], "outcome": "settled"},
                {"payment_id": "pay_doesnotexist", "outcome": "settled"},
                {"payment_id": captured_payment["id"], "outcome": "settled"},
            ]
        },
        headers=auth,
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["accepted"] == 1
    assert body["rejected"] == 2
    assert body["errors"][0]["index"] == 1
    # The third item repeats the first payment_id, which is no longer
    # `captured` after the first item settled it -> rejected too.
    assert body["errors"][1]["index"] == 2


def test_settling_a_non_captured_payment_is_rejected(client, auth, payment):
    response = client.post(
        "/v1/settlements",
        json={"settlements": [{"payment_id": payment["id"], "outcome": "settled"}]},
        headers=auth,
    )
    assert response.status_code == 400
    assert response.get_json()["accepted"] == 0


def test_all_invalid_returns_400(client, auth):
    response = client.post(
        "/v1/settlements",
        json={"settlements": [{"payment_id": "pay_x"}]},
        headers=auth,
    )
    assert response.status_code == 400


def test_empty_settlements_is_400(client, auth):
    response = client.post("/v1/settlements", json={"settlements": []}, headers=auth)
    assert response.status_code == 400


def test_oversized_batch_is_413(client, auth):
    settlements = [{"payment_id": "pay_x", "outcome": "settled"} for _ in range(501)]
    response = client.post(
        "/v1/settlements", json={"settlements": settlements}, headers=auth
    )
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "payload_too_large"


def test_settlement_is_scoped_to_the_reporting_tenant(
    client, auth, other_auth, captured_payment
):
    """A merchant cannot settle another merchant's payment."""
    response = client.post(
        "/v1/settlements",
        json={"settlements": [{"payment_id": captured_payment["id"], "outcome": "settled"}]},
        headers=other_auth,
    )
    assert response.status_code == 400
    assert response.get_json()["errors"][0]["reason"] == "payment not found"
