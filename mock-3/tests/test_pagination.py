"""Cursor pagination."""

import pytest

from app.core.pagination import decode_cursor, encode_cursor, paginate


@pytest.fixture
def many(client, auth):
    for i in range(12):
        client.post(
            "/v1/cases",
            json={
                "external_id": "post_{:02d}".format(i),
                "kind": "post",
                "text": "content {}".format(i),
            },
            headers=auth,
        )


def test_default_limit_and_no_cursor(client, auth, many):
    body = client.get("/v1/cases", headers=auth).get_json()
    assert len(body["data"]) == 12
    assert body["pagination"]["has_more"] is False
    assert body["pagination"]["next_cursor"] is None


def test_limit_produces_a_cursor(client, auth, many):
    body = client.get("/v1/cases?limit=5", headers=auth).get_json()
    assert len(body["data"]) == 5
    assert body["pagination"]["has_more"] is True
    assert body["pagination"]["next_cursor"]


def test_walking_every_page_sees_each_record_once(client, auth, many):
    seen = []
    cursor = None
    for _ in range(10):
        url = "/v1/cases?limit=5"
        if cursor:
            url += "&cursor={}".format(cursor)
        body = client.get(url, headers=auth).get_json()
        seen.extend(c["id"] for c in body["data"])
        cursor = body["pagination"]["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 12
    assert len(set(seen)) == 12


def test_limit_is_clamped_not_rejected(client, auth, many):
    body = client.get("/v1/cases?limit=100000", headers=auth).get_json()
    assert body["pagination"]["limit"] == 100


def test_limit_zero_is_400(client, auth):
    assert client.get("/v1/cases?limit=0", headers=auth).status_code == 400


def test_non_integer_limit_is_400(client, auth):
    assert client.get("/v1/cases?limit=lots", headers=auth).status_code == 400


def test_malformed_cursor_is_400(client, auth):
    response = client.get("/v1/cases?cursor=!!!notbase64!!!", headers=auth)
    assert response.status_code == 400
    assert response.get_json()["error"]["errors"][0]["field"] == "cursor"


def test_cursor_roundtrip():
    payload = {"created_at": "2026-01-01T00:00:00.000Z", "id": "cas_x"}
    assert decode_cursor(encode_cursor(payload)) == payload


def test_deleted_anchor_still_advances():
    records = [
        {"id": "c", "created_at": "2026-01-03T00:00:00.000Z"},
        {"id": "a", "created_at": "2026-01-01T00:00:00.000Z"},
    ]
    cursor = encode_cursor({"created_at": "2026-01-02T00:00:00.000Z", "id": "b"})
    page, _ = paginate(records, limit=10, cursor=cursor)
    assert [r["id"] for r in page] == ["a"]
