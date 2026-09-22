"""CRUD, validation, concurrency, and project scoping on builds."""


def test_create_returns_201_with_location_and_etag(client):
    response = client.post(
        "/v1/projects/web/builds",
        json={"branch": "main", "commit": "a1b2c3d4e5f6"},
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["state"] == "queued"
    assert body["trigger"] == "push"
    assert body["jobs"] == ["build"]
    assert response.headers["Location"].endswith(body["id"])
    assert response.headers["ETag"] == 'W/"{}-1"'.format(body["id"])


def test_create_collects_every_field_error(client):
    response = client.post(
        "/v1/projects/web/builds",
        json={"branch": "", "commit": "abc", "trigger": "telepathy"},
    )
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["error"]["errors"]}
    assert fields == {"branch", "commit", "trigger"}


def test_create_rejects_non_object_body(client):
    assert client.post("/v1/projects/web/builds", json=[1, 2]).status_code == 400


def test_get_returns_the_build(client, build):
    response = client.get("/v1/projects/web/builds/{}".format(build["id"]))
    assert response.status_code == 200
    assert response.get_json()["commit"] == "a1b2c3d4e5f6"


def test_get_unknown_build_is_404_in_the_error_envelope(client):
    response = client.get("/v1/projects/web/builds/bld_nope")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_build_from_another_project_is_404_not_403(client, build):
    response = client.get("/v1/projects/api/builds/{}".format(build["id"]))
    assert response.status_code == 404


def test_unknown_project_is_404(client):
    response = client.get("/v1/projects/ghost/builds")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "project_not_found"


def test_conditional_get_returns_304(client, build):
    etag = 'W/"{}-1"'.format(build["id"])
    response = client.get(
        "/v1/projects/web/builds/{}".format(build["id"]),
        headers={"If-None-Match": etag},
    )
    assert response.status_code == 304


def test_patch_updates_metadata_and_bumps_version(client, build):
    response = client.patch(
        "/v1/projects/web/builds/{}".format(build["id"]),
        json={"branch": "release"},
    )
    assert response.status_code == 200
    assert response.get_json()["branch"] == "release"
    assert response.get_json()["version"] == 2


def test_patch_cannot_write_state(client, build):
    response = client.patch(
        "/v1/projects/web/builds/{}".format(build["id"]),
        json={"state": "passed"},
    )
    assert response.status_code == 400


def test_stale_if_match_is_409(client, build):
    url = "/v1/projects/web/builds/{}".format(build["id"])
    client.patch(url, json={"branch": "one"})
    response = client.patch(
        url,
        json={"branch": "two"},
        headers={"If-Match": 'W/"{}-1"'.format(build["id"])},
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "version_conflict"


def test_matching_if_match_succeeds(client, build):
    response = client.patch(
        "/v1/projects/web/builds/{}".format(build["id"]),
        json={"branch": "two"},
        headers={"If-Match": 'W/"{}-1"'.format(build["id"])},
    )
    assert response.status_code == 200


def test_malformed_if_match_is_400(client, build):
    response = client.patch(
        "/v1/projects/web/builds/{}".format(build["id"]),
        json={"branch": "two"},
        headers={"If-Match": "garbage"},
    )
    assert response.status_code == 400


def test_idempotency_key_replays_the_original_build(client):
    headers = {"Idempotency-Key": "abc-123"}
    payload = {"branch": "main", "commit": "a1b2c3d4e5f6"}
    first = client.post("/v1/projects/web/builds", json=payload, headers=headers)
    second = client.post("/v1/projects/web/builds", json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"
    assert first.get_json()["id"] == second.get_json()["id"]


def test_method_not_allowed_uses_the_error_envelope(client):
    response = client.delete("/v1/projects/web/builds")
    assert response.status_code == 405
    assert "error" in response.get_json()
