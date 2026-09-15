def _create(client, auth, service, **overrides):
    body = {"title": "SM test", "severity": "sev2", "service_id": service["id"]}
    body.update(overrides)
    return client.post("/v1/incidents", json=body, headers=auth).get_json()


def test_valid_transition_sequence(client, auth, service):
    incident = _create(client, auth, service)
    iid = incident["id"]

    for status in ("acknowledged", "investigating", "monitoring", "resolved"):
        response = client.post(
            "/v1/incidents/{}/status".format(iid), json={"status": status}, headers=auth
        )
        assert response.status_code == 200, status
        assert response.get_json()["status"] == status

    resolved = client.get("/v1/incidents/{}".format(iid), headers=auth).get_json()
    assert resolved["resolved_at"] is not None


def test_same_state_transition_is_noop(client, auth, service):
    incident = _create(client, auth, service)
    iid = incident["id"]
    before_version = incident["version"]
    response = client.post(
        "/v1/incidents/{}/status".format(iid), json={"status": "triggered"}, headers=auth
    )
    assert response.status_code == 200
    assert response.get_json()["version"] == before_version

    timeline = client.get("/v1/incidents/{}/timeline".format(iid), headers=auth).get_json()
    assert len(timeline["data"]) == 1  # only the "created" entry -- no duplicate


def test_invalid_transition_is_409(client, auth, service):
    incident = _create(client, auth, service)
    response = client.post(
        "/v1/incidents/{}/status".format(incident["id"]),
        json={"status": "monitoring"},
        headers=auth,
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "invalid_state_transition"


def test_resolve_then_reopen_clears_resolved_at(client, auth, service):
    incident = _create(client, auth, service)
    iid = incident["id"]
    client.post("/v1/incidents/{}/status".format(iid), json={"status": "resolved"}, headers=auth)
    reopened = client.post(
        "/v1/incidents/{}/status".format(iid), json={"status": "triggered"}, headers=auth
    )
    assert reopened.status_code == 200
    assert reopened.get_json()["resolved_at"] is None


def test_cannot_leave_resolved_except_to_triggered(client, auth, service):
    incident = _create(client, auth, service)
    iid = incident["id"]
    client.post("/v1/incidents/{}/status".format(iid), json={"status": "resolved"}, headers=auth)
    response = client.post(
        "/v1/incidents/{}/status".format(iid), json={"status": "acknowledged"}, headers=auth
    )
    assert response.status_code == 409


def test_status_transition_records_timeline(client, auth, service):
    incident = _create(client, auth, service)
    iid = incident["id"]
    client.post("/v1/incidents/{}/status".format(iid), json={"status": "acknowledged"}, headers=auth)
    timeline = client.get("/v1/incidents/{}/timeline".format(iid), headers=auth).get_json()
    kinds = [e["kind"] for e in timeline["data"]]
    assert "status_change" in kinds


def test_invalid_status_value(client, auth, service):
    incident = _create(client, auth, service)
    response = client.post(
        "/v1/incidents/{}/status".format(incident["id"]),
        json={"status": "on_fire"},
        headers=auth,
    )
    assert response.status_code == 400


def test_status_transition_other_org_is_404(client, other_auth, auth, service):
    incident = _create(client, auth, service)
    response = client.post(
        "/v1/incidents/{}/status".format(incident["id"]),
        json={"status": "acknowledged"},
        headers=other_auth,
    )
    assert response.status_code == 404
