"""Payment status transitions: capture, void, and the settlement boundary."""


def test_capture_sets_captured_at(client, auth, payment):
    response = client.post(
        "/v1/payments/{}/status".format(payment["id"]),
        json={"status": "captured"},
        headers=auth,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "captured"
    assert body["captured_at"] is not None


def test_void_sets_voided_at(client, auth, payment):
    response = client.post(
        "/v1/payments/{}/status".format(payment["id"]),
        json={"status": "voided"},
        headers=auth,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "voided"
    assert body["voided_at"] is not None


def test_repeated_transition_is_a_no_op(client, auth, captured_payment):
    before_version = captured_payment["version"]
    response = client.post(
        "/v1/payments/{}/status".format(captured_payment["id"]),
        json={"status": "captured"},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.get_json()["version"] == before_version


def test_voiding_a_captured_payment_is_409(client, auth, captured_payment):
    response = client.post(
        "/v1/payments/{}/status".format(captured_payment["id"]),
        json={"status": "voided"},
        headers=auth,
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "invalid_state_transition"


def test_voiding_a_voided_payment_is_409(client, auth, payment):
    client.post(
        "/v1/payments/{}/status".format(payment["id"]),
        json={"status": "voided"},
        headers=auth,
    )
    response = client.post(
        "/v1/payments/{}/status".format(payment["id"]),
        json={"status": "captured"},
        headers=auth,
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "terminal_state"


def test_cannot_settle_directly(client, auth, captured_payment):
    response = client.post(
        "/v1/payments/{}/status".format(captured_payment["id"]),
        json={"status": "settled"},
        headers=auth,
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "settlement_is_automated"


def test_cannot_fail_directly(client, auth, captured_payment):
    response = client.post(
        "/v1/payments/{}/status".format(captured_payment["id"]),
        json={"status": "failed"},
        headers=auth,
    )
    assert response.status_code == 409


def test_cannot_move_back_to_authorized(client, auth, captured_payment):
    response = client.post(
        "/v1/payments/{}/status".format(captured_payment["id"]),
        json={"status": "authorized"},
        headers=auth,
    )
    assert response.status_code == 409


def test_invalid_status_is_400(client, auth, payment):
    response = client.post(
        "/v1/payments/{}/status".format(payment["id"]),
        json={"status": "on_fire"},
        headers=auth,
    )
    assert response.status_code == 400
