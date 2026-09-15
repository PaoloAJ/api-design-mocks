"""Cursor pagination behavior, including the stability property that motivates it."""

import pytest

from app.core.pagination import MAX_LIMIT, decode_cursor, encode_cursor, paginate
from app.core.errors import ValidationError


def _make(client, auth, count):
    for i in range(count):
        response = client.post(
            "/v1/monitors",
            json={
                "name": "Monitor {:02d}".format(i),
                "type": "metric",
                "query": "avg:cpu{{*}} > {}".format(i),
            },
            headers=auth,
        )
        assert response.status_code == 201


def test_default_limit_and_has_more(client, auth):
    _make(client, auth, 30)
    body = client.get("/v1/monitors", headers=auth).get_json()
    assert len(body["data"]) == 25
    assert body["pagination"]["has_more"] is True
    assert body["pagination"]["next_cursor"]


def test_walking_cursors_yields_every_record_exactly_once(client, auth):
    _make(client, auth, 30)
    seen = []
    cursor = None
    for _ in range(10):
        url = "/v1/monitors?limit=7"
        if cursor:
            url += "&cursor={}".format(cursor)
        body = client.get(url, headers=auth).get_json()
        seen.extend(m["id"] for m in body["data"])
        cursor = body["pagination"]["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 30
    assert len(set(seen)) == 30


def test_limit_is_clamped_not_rejected(client, auth):
    _make(client, auth, 3)
    body = client.get("/v1/monitors?limit=100000", headers=auth).get_json()
    assert body["pagination"]["limit"] == MAX_LIMIT


def test_zero_limit_is_400(client, auth):
    assert client.get("/v1/monitors?limit=0", headers=auth).status_code == 400


def test_non_integer_limit_is_400(client, auth):
    assert client.get("/v1/monitors?limit=abc", headers=auth).status_code == 400


def test_malformed_cursor_is_400(client, auth):
    response = client.get("/v1/monitors?cursor=!!!notbase64!!!", headers=auth)
    assert response.status_code == 400


def test_cursor_roundtrip():
    payload = {"created_at": "2026-01-01T00:00:00.000Z", "id": "mon_x"}
    assert decode_cursor(encode_cursor(payload)) == payload


def test_deleted_anchor_still_advances():
    """A cursor whose anchor row was deleted must not restart from the top."""
    records = [
        {"id": "c", "created_at": "2026-01-03T00:00:00.000Z"},
        {"id": "a", "created_at": "2026-01-01T00:00:00.000Z"},
    ]
    cursor = encode_cursor({"created_at": "2026-01-02T00:00:00.000Z", "id": "b"})
    page, _ = paginate(records, limit=10, cursor=cursor)
    assert [r["id"] for r in page] == ["a"]
