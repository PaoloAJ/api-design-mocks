"""Alert lifecycle driven by monitor state transitions."""


def test_transition_to_alert_opens_an_alert(client, auth, monitor):
    response = client.post(
        "/v1/monitors/{}/status".format(monitor["id"]),
        json={"status": "alert"},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "alert"

    alerts = client.get("/v1/alerts", headers=auth).get_json()["data"]
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["state"] == "open"


def test_repeated_transition_does_not_duplicate_alerts(client, auth, monitor):
    for _ in range(3):
        client.post(
            "/v1/monitors/{}/status".format(monitor["id"]),
            json={"status": "alert"},
            headers=auth,
        )
    alerts = client.get("/v1/alerts", headers=auth).get_json()["data"]
    assert len(alerts) == 1


def test_returning_to_ok_resolves_open_alerts(client, auth, monitor):
    client.post(
        "/v1/monitors/{}/status".format(monitor["id"]),
        json={"status": "alert"},
        headers=auth,
    )
    client.post(
        "/v1/monitors/{}/status".format(monitor["id"]),
        json={"status": "ok"},
        headers=auth,
    )
    alerts = client.get("/v1/alerts", headers=auth).get_json()["data"]
    assert alerts[0]["state"] == "resolved"
    assert alerts[0]["resolved_at"] is not None


def test_invalid_status_is_400(client, auth, monitor):
    response = client.post(
        "/v1/monitors/{}/status".format(monitor["id"]),
        json={"status": "on_fire"},
        headers=auth,
    )
    assert response.status_code == 400


def test_acknowledge_requires_user(client, auth, monitor):
    client.post(
        "/v1/monitors/{}/status".format(monitor["id"]),
        json={"status": "alert"},
        headers=auth,
    )
    alert = client.get("/v1/alerts", headers=auth).get_json()["data"][0]
    response = client.post(
        "/v1/alerts/{}/acknowledge".format(alert["id"]), json={}, headers=auth
    )
    assert response.status_code == 400


def test_acknowledge_is_idempotent(client, auth, monitor):
    client.post(
        "/v1/monitors/{}/status".format(monitor["id"]),
        json={"status": "alert"},
        headers=auth,
    )
    alert = client.get("/v1/alerts", headers=auth).get_json()["data"][0]
    payload = {"user": "paolo"}
    first = client.post(
        "/v1/alerts/{}/acknowledge".format(alert["id"]), json=payload, headers=auth
    )
    second = client.post(
        "/v1/alerts/{}/acknowledge".format(alert["id"]), json=payload, headers=auth
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["state"] == "acknowledged"


def test_acknowledging_resolved_alert_is_409(client, auth, monitor):
    client.post(
        "/v1/monitors/{}/status".format(monitor["id"]),
        json={"status": "alert"},
        headers=auth,
    )
    client.post(
        "/v1/monitors/{}/status".format(monitor["id"]),
        json={"status": "ok"},
        headers=auth,
    )
    alert = client.get("/v1/alerts", headers=auth).get_json()["data"][0]
    response = client.post(
        "/v1/alerts/{}/acknowledge".format(alert["id"]),
        json={"user": "paolo"},
        headers=auth,
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "invalid_state_transition"


def test_nested_alerts_route_scopes_to_monitor(client, auth, monitor):
    client.post(
        "/v1/monitors/{}/status".format(monitor["id"]),
        json={"status": "warn"},
        headers=auth,
    )
    body = client.get(
        "/v1/monitors/{}/alerts".format(monitor["id"]), headers=auth
    ).get_json()
    assert len(body["data"]) == 1
    assert body["data"][0]["monitor_id"] == monitor["id"]
