def test_create_incident(client, auth, service):
    response = client.post(
        "/v1/incidents",
        json={
            "title": "DB failover stuck",
            "severity": "sev1",
            "service_id": service["id"],
            "commander": "priya",
        },
        headers=auth,
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["status"] == "triggered"
    assert body["responders"] == ["priya"]
    assert body["resolved_at"] is None
    assert "ETag" in response.headers
    assert response.headers["Location"] == "/v1/incidents/{}".format(body["id"])


def test_create_incident_auto_timeline_entries(client, auth, service):
    response = client.post(
        "/v1/incidents",
        json={
            "title": "x",
            "severity": "sev3",
            "service_id": service["id"],
            "commander": "ada",
        },
        headers=auth,
    )
    incident_id = response.get_json()["id"]
    timeline = client.get(
        "/v1/incidents/{}/timeline".format(incident_id), headers=auth
    ).get_json()
    kinds = [e["kind"] for e in timeline["data"]]
    assert "created" in kinds
    assert "responder_added" in kinds


def test_create_incident_missing_fields(client, auth):
    response = client.post("/v1/incidents", json={}, headers=auth)
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["error"]["errors"]}
    assert {"title", "severity", "service_id"} <= fields


def test_create_incident_unknown_field(client, auth, service):
    response = client.post(
        "/v1/incidents",
        json={
            "title": "x",
            "severity": "sev3",
            "service_id": service["id"],
            "sevrity": "sev1",
        },
        headers=auth,
    )
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["error"]["errors"]}
    assert "sevrity" in fields


def test_create_incident_bad_severity(client, auth, service):
    response = client.post(
        "/v1/incidents",
        json={"title": "x", "severity": "critical", "service_id": service["id"]},
        headers=auth,
    )
    assert response.status_code == 400


def test_create_incident_unknown_service(client, auth):
    response = client.post(
        "/v1/incidents",
        json={"title": "x", "severity": "sev3", "service_id": "svc_nope"},
        headers=auth,
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["errors"][0]["field"] == "service_id"


def test_create_incident_idempotency_key_replays(client, auth, service):
    body = {"title": "Replay me", "severity": "sev2", "service_id": service["id"]}
    headers = dict(auth, **{"Idempotency-Key": "key-1"})
    first = client.post("/v1/incidents", json=body, headers=headers)
    second = client.post("/v1/incidents", json=body, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers.get("Idempotent-Replay") == "true"
    assert first.get_json()["id"] == second.get_json()["id"]

    listing = client.get("/v1/incidents", headers=auth).get_json()
    assert len(listing["data"]) == 1


def test_get_incident(client, auth, incident):
    response = client.get("/v1/incidents/{}".format(incident["id"]), headers=auth)
    assert response.status_code == 200
    assert response.get_json()["id"] == incident["id"]


def test_get_incident_if_none_match_304(client, auth, incident):
    etag = client.get(
        "/v1/incidents/{}".format(incident["id"]), headers=auth
    ).headers["ETag"]
    response = client.get(
        "/v1/incidents/{}".format(incident["id"]),
        headers=dict(auth, **{"If-None-Match": etag}),
    )
    assert response.status_code == 304


def test_get_incident_other_org_is_404(client, other_auth, incident):
    response = client.get(
        "/v1/incidents/{}".format(incident["id"]), headers=other_auth
    )
    assert response.status_code == 404


def test_get_missing_incident_is_404(client, auth):
    response = client.get("/v1/incidents/inc_doesnotexist", headers=auth)
    assert response.status_code == 404


def test_patch_incident(client, auth, incident):
    response = client.patch(
        "/v1/incidents/{}".format(incident["id"]),
        json={"title": "Updated title", "tags": ["env:prod"]},
        headers=auth,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["title"] == "Updated title"
    assert body["tags"] == ["env:prod"]
    assert body["version"] == incident["version"] + 1


def test_patch_incident_rejects_severity(client, auth, incident):
    response = client.patch(
        "/v1/incidents/{}".format(incident["id"]),
        json={"severity": "sev1"},
        headers=auth,
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["errors"][0]["field"] == "severity"


def test_patch_incident_rejects_status(client, auth, incident):
    response = client.patch(
        "/v1/incidents/{}".format(incident["id"]),
        json={"status": "resolved"},
        headers=auth,
    )
    assert response.status_code == 400


def test_patch_incident_stale_if_match_conflicts(client, auth, incident):
    etag = 'W/"{}-999"'.format(incident["id"])
    response = client.patch(
        "/v1/incidents/{}".format(incident["id"]),
        json={"title": "New"},
        headers=dict(auth, **{"If-Match": etag}),
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "version_conflict"


def test_patch_incident_requires_a_field(client, auth, incident):
    response = client.patch(
        "/v1/incidents/{}".format(incident["id"]), json={}, headers=auth
    )
    assert response.status_code == 400


def test_patch_incident_can_clear_commander(client, auth, incident):
    response = client.patch(
        "/v1/incidents/{}".format(incident["id"]),
        json={"commander": None},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.get_json()["commander"] is None


def test_no_delete_route_for_incidents(client, auth, incident):
    response = client.delete(
        "/v1/incidents/{}".format(incident["id"]), headers=auth
    )
    assert response.status_code == 405
