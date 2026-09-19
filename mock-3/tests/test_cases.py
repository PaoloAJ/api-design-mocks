"""CRUD, concurrency, idempotency and tenant isolation on /v1/cases."""


def test_create_returns_201_with_location_and_etag(client, auth):
    response = client.post(
        "/v1/cases",
        json={"external_id": "post_9", "kind": "post", "text": "hello"},
        headers=auth,
    )
    assert response.status_code == 201
    body = response.get_json()
    assert response.headers["Location"] == "/v1/cases/{}".format(body["id"])
    assert response.headers["ETag"] == 'W/"{}-1"'.format(body["id"])
    assert body["state"] == "pending"
    assert body["queue"] == "standard"


def test_create_collects_every_field_error(client, auth):
    response = client.post(
        "/v1/cases",
        json={"external_id": "", "kind": "hologram", "text": "", "reason": "vibes"},
        headers=auth,
    )
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["error"]["errors"]}
    assert {"external_id", "kind", "text", "reason"} <= fields


def test_create_rejects_unknown_field(client, auth):
    response = client.post(
        "/v1/cases",
        json={"external_id": "p", "kind": "post", "text": "t", "raeson": "spam"},
        headers=auth,
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["errors"][0]["field"] == "raeson"


def test_duplicate_external_id_is_409(client, auth, case):
    response = client.post(
        "/v1/cases",
        json={"external_id": "post_1", "kind": "post", "text": "again"},
        headers=auth,
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "duplicate_case"


def test_idempotency_key_replays_the_original(client, auth):
    payload = {"external_id": "post_2", "kind": "post", "text": "once"}
    headers = dict(auth, **{"Idempotency-Key": "key-abc"})
    first = client.post("/v1/cases", json=payload, headers=headers)
    second = client.post("/v1/cases", json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.get_json()["id"] == first.get_json()["id"]


def test_get_unknown_case_is_404_with_envelope(client, auth):
    response = client.get("/v1/cases/cas_nope", headers=auth)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_if_none_match_returns_304(client, auth, case):
    etag = 'W/"{}-1"'.format(case["id"])
    response = client.get(
        "/v1/cases/{}".format(case["id"]),
        headers=dict(auth, **{"If-None-Match": etag}),
    )
    assert response.status_code == 304
    assert response.get_data() == b""


def test_patch_updates_only_supplied_fields(client, auth, case):
    response = client.patch(
        "/v1/cases/{}".format(case["id"]), json={"queue": "priority"}, headers=auth
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["queue"] == "priority"
    assert body["text"] == case["text"]
    assert body["version"] == 2


def test_patch_cannot_write_state(client, auth, case):
    response = client.patch(
        "/v1/cases/{}".format(case["id"]), json={"state": "approved"}, headers=auth
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["errors"][0]["field"] == "state"


def test_patch_with_empty_body_is_400(client, auth, case):
    response = client.patch("/v1/cases/{}".format(case["id"]), json={}, headers=auth)
    assert response.status_code == 400


def test_stale_if_match_is_409(client, auth, case):
    client.patch("/v1/cases/{}".format(case["id"]), json={"queue": "legal"}, headers=auth)
    response = client.patch(
        "/v1/cases/{}".format(case["id"]),
        json={"queue": "standard"},
        headers=dict(auth, **{"If-Match": 'W/"{}-1"'.format(case["id"])}),
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "version_conflict"


def test_current_if_match_succeeds(client, auth, case):
    response = client.patch(
        "/v1/cases/{}".format(case["id"]),
        json={"queue": "legal"},
        headers=dict(auth, **{"If-Match": 'W/"{}-1"'.format(case["id"])}),
    )
    assert response.status_code == 200


def test_malformed_if_match_is_400(client, auth, case):
    response = client.patch(
        "/v1/cases/{}".format(case["id"]),
        json={"queue": "legal"},
        headers=dict(auth, **{"If-Match": "garbage"}),
    )
    assert response.status_code == 400


def test_delete_then_delete_is_204_then_404(client, auth, case):
    assert client.delete("/v1/cases/{}".format(case["id"]), headers=auth).status_code == 204
    assert client.delete("/v1/cases/{}".format(case["id"]), headers=auth).status_code == 404


def test_other_tenant_gets_404_not_403(client, auth, other_auth, case):
    response = client.get("/v1/cases/{}".format(case["id"]), headers=other_auth)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_missing_api_key_is_401(client):
    response = client.get("/v1/cases")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "missing_api_key"



def test_health_needs_no_auth(client):
    assert client.get("/health").status_code == 200


def test_unknown_route_returns_json_envelope(client, auth):
    response = client.get("/v1/nope", headers=auth)
    assert response.status_code == 404
    assert "error" in response.get_json()


def test_request_id_is_echoed(client, auth):
    response = client.get("/v1/cases", headers=dict(auth, **{"X-Request-ID": "rid-1"}))
    assert response.headers["X-Request-ID"] == "rid-1"
