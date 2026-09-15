"""Contract tests for /v1/monitors."""


def test_health_needs_no_auth(client):
    assert client.get("/health").status_code == 200


def test_missing_api_key_is_401(client):
    response = client.get("/v1/monitors")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "missing_api_key"


def test_bad_api_key_is_401(client):
    response = client.get("/v1/monitors", headers={"DD-API-KEY": "nope"})
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_api_key"


def test_create_returns_201_with_location_and_etag(client, auth):
    response = client.post(
        "/v1/monitors",
        json={"name": "CPU", "type": "metric", "query": "avg:cpu{*} > 90"},
        headers=auth,
    )
    assert response.status_code == 201
    assert response.headers["Location"].startswith("/v1/monitors/mon_")
    assert response.headers["ETag"].startswith('W/"mon_')
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["version"] == 1


def test_create_collects_all_validation_errors(client, auth):
    response = client.post("/v1/monitors", json={"type": "bogus"}, headers=auth)
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["error"]["errors"]}
    assert {"name", "type", "query"} <= fields


def test_unknown_field_is_rejected(client, auth):
    response = client.post(
        "/v1/monitors",
        json={"name": "X", "type": "metric", "query": "q", "nmae": "typo"},
        headers=auth,
    )
    assert response.status_code == 400
    assert any(
        e["field"] == "nmae" for e in response.get_json()["error"]["errors"]
    )


def test_warning_must_be_below_critical(client, auth):
    response = client.post(
        "/v1/monitors",
        json={
            "name": "X",
            "type": "metric",
            "query": "q",
            "thresholds": {"warning": 90, "critical": 50},
        },
        headers=auth,
    )
    assert response.status_code == 400


def test_duplicate_name_is_409(client, auth, monitor):
    response = client.post(
        "/v1/monitors",
        json={"name": monitor["name"], "type": "metric", "query": "q"},
        headers=auth,
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "duplicate_monitor"


def test_idempotency_key_replays_instead_of_duplicating(client, auth):
    payload = {"name": "Idem", "type": "metric", "query": "q"}
    headers = dict(auth)
    headers["Idempotency-Key"] = "abc-123"

    first = client.post("/v1/monitors", json=payload, headers=headers)
    second = client.post("/v1/monitors", json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"
    assert first.get_json()["id"] == second.get_json()["id"]


def test_get_missing_monitor_is_404_json(client, auth):
    response = client.get("/v1/monitors/mon_doesnotexist", headers=auth)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_if_none_match_returns_304(client, auth, monitor):
    etag = client.get(
        "/v1/monitors/{}".format(monitor["id"]), headers=auth
    ).headers["ETag"]
    headers = dict(auth)
    headers["If-None-Match"] = etag
    response = client.get("/v1/monitors/{}".format(monitor["id"]), headers=headers)
    assert response.status_code == 304


def test_patch_updates_only_supplied_fields(client, auth, monitor):
    response = client.patch(
        "/v1/monitors/{}".format(monitor["id"]),
        json={"enabled": False},
        headers=auth,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["enabled"] is False
    assert body["name"] == monitor["name"]
    assert body["version"] == monitor["version"] + 1


def test_patch_with_empty_body_is_400(client, auth, monitor):
    response = client.patch(
        "/v1/monitors/{}".format(monitor["id"]), json={}, headers=auth
    )
    assert response.status_code == 400


def test_stale_if_match_is_409(client, auth, monitor):
    headers = dict(auth)
    headers["If-Match"] = 'W/"{}-1"'.format(monitor["id"])

    first = client.patch(
        "/v1/monitors/{}".format(monitor["id"]),
        json={"name": "Renamed once"},
        headers=headers,
    )
    assert first.status_code == 200

    # Same stale version replayed: the resource is now at version 2.
    second = client.patch(
        "/v1/monitors/{}".format(monitor["id"]),
        json={"name": "Renamed twice"},
        headers=headers,
    )
    assert second.status_code == 409
    assert second.get_json()["error"]["code"] == "version_conflict"


def test_delete_returns_204_then_404(client, auth, monitor):
    assert client.delete(
        "/v1/monitors/{}".format(monitor["id"]), headers=auth
    ).status_code == 204
    assert client.delete(
        "/v1/monitors/{}".format(monitor["id"]), headers=auth
    ).status_code == 404


def test_tenant_isolation_hides_other_org(client, auth, other_auth, monitor):
    response = client.get("/v1/monitors/{}".format(monitor["id"]), headers=other_auth)
    assert response.status_code == 404  # 404 not 403, to avoid leaking existence


def test_request_id_is_echoed(client, auth):
    response = client.get("/health", headers={"X-Request-ID": "trace-me"})
    assert response.headers["X-Request-ID"] == "trace-me"


def test_method_not_allowed_is_json(client, auth):
    response = client.put("/v1/monitors", json={}, headers=auth)
    assert response.status_code == 405
    assert response.get_json()["error"]["code"]
