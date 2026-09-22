"""Cursor pagination and collection filtering."""

from app.core.pagination import decode_cursor, encode_cursor, paginate


def _make(client, count, **overrides):
    for i in range(count):
        payload = {"branch": "main", "commit": "commit{:08d}".format(i)}
        payload.update(overrides)
        assert client.post("/v1/projects/web/builds", json=payload).status_code == 201


def test_default_limit_applies(client):
    _make(client, 3)
    body = client.get("/v1/projects/web/builds").get_json()
    assert body["pagination"]["limit"] == 20
    assert body["pagination"]["has_more"] is False


def test_limit_is_clamped_not_rejected(client):
    body = client.get("/v1/projects/web/builds?limit=5000").get_json()
    assert body["pagination"]["limit"] == 100


def test_non_integer_limit_is_400(client):
    assert client.get("/v1/projects/web/builds?limit=lots").status_code == 400


def test_zero_limit_is_400(client):
    assert client.get("/v1/projects/web/builds?limit=0").status_code == 400


def test_walk_visits_every_build_once(client):
    _make(client, 12)
    seen = []
    cursor = None
    for _ in range(10):
        url = "/v1/projects/web/builds?limit=5"
        if cursor:
            url += "&cursor={}".format(cursor)
        body = client.get(url).get_json()
        seen.extend(b["id"] for b in body["data"])
        cursor = body["pagination"]["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 12
    assert len(set(seen)) == 12


def test_malformed_cursor_is_400(client):
    response = client.get("/v1/projects/web/builds?cursor=!!!not-base64!!!")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"


def test_cursor_missing_sort_key_is_400(client):
    cursor = encode_cursor({"id": "bld_x"})
    assert client.get(
        "/v1/projects/web/builds?cursor={}".format(cursor)
    ).status_code == 400


def test_cursor_roundtrip():
    payload = {"created_at": "2026-01-01T00:00:00.000Z", "id": "bld_x"}
    assert decode_cursor(encode_cursor(payload)) == payload


def test_deleted_anchor_still_advances():
    records = [
        {"id": "c", "created_at": "2026-01-03T00:00:00.000Z"},
        {"id": "a", "created_at": "2026-01-01T00:00:00.000Z"},
    ]
    cursor = encode_cursor({"created_at": "2026-01-02T00:00:00.000Z", "id": "b"})
    page, _ = paginate(records, limit=10, cursor=cursor)
    assert [r["id"] for r in page] == ["a"]


def test_filter_by_state(client):
    _make(client, 3)
    body = client.get("/v1/projects/web/builds?state=queued").get_json()
    assert len(body["data"]) == 3
    assert client.get(
        "/v1/projects/web/builds?state=passed"
    ).get_json()["data"] == []


def test_filter_by_branch(client):
    _make(client, 2)
    _make(client, 1, branch="release")
    body = client.get("/v1/projects/web/builds?branch=release").get_json()
    assert len(body["data"]) == 1


def test_filter_by_trigger(client):
    _make(client, 2)
    _make(client, 1, trigger="manual")
    body = client.get("/v1/projects/web/builds?trigger=manual").get_json()
    assert len(body["data"]) == 1


def test_invalid_state_filter_is_400(client):
    assert client.get("/v1/projects/web/builds?state=sideways").status_code == 400


def test_invalid_trigger_filter_is_400(client):
    assert client.get("/v1/projects/web/builds?trigger=psychic").status_code == 400


def test_filter_runs_before_pagination(client):
    _make(client, 6)
    _make(client, 4, branch="release")
    body = client.get("/v1/projects/web/builds?branch=release&limit=3").get_json()
    assert len(body["data"]) == 3
    assert body["pagination"]["has_more"] is True
    assert all(b["branch"] == "release" for b in body["data"])


def test_list_is_scoped_to_the_project(client):
    _make(client, 3)
    assert client.get("/v1/projects/api/builds").get_json()["data"] == []
