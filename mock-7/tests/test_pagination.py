"""Cursor pagination and template updates."""

from __future__ import annotations

from tests.conftest import ACME_AUTH, send


def test_limit_is_respected(client, template_id):
    for _ in range(5):
        send(client, template_id)
    body = client.get("/v1/messages?limit=2", headers=ACME_AUTH).get_json()
    assert len(body["data"]) == 2
    assert body["has_more"] is True


def test_walking_pages_visits_every_row_once(client, template_id):
    for index in range(7):
        send(client, template_id, to="user{}@example.com".format(index))

    seen, cursor = [], None
    for _ in range(20):
        url = "/v1/messages?limit=3"
        if cursor:
            url += "&cursor=" + cursor
        body = client.get(url, headers=ACME_AUTH).get_json()
        seen.extend(m["id"] for m in body["data"])
        cursor = body["next_cursor"]
        if not cursor:
            break

    assert len(seen) == len(set(seen))
    total = client.get("/v1/messages?limit=100", headers=ACME_AUTH).get_json()
    assert len(seen) == len(total["data"])


def test_last_page_has_no_cursor(client, template_id):
    body = client.get("/v1/messages?limit=100", headers=ACME_AUTH).get_json()
    assert body["next_cursor"] is None
    assert body["has_more"] is False


def test_malformed_cursor_is_400(client):
    response = client.get("/v1/messages?cursor=!!!!", headers=ACME_AUTH)
    assert response.status_code == 400


def test_limit_is_clamped_not_rejected(client):
    response = client.get("/v1/messages?limit=5000", headers=ACME_AUTH)
    assert response.status_code == 200


def test_zero_limit_is_400(client):
    response = client.get("/v1/messages?limit=0", headers=ACME_AUTH)
    assert response.status_code == 400


def test_non_integer_limit_is_400(client):
    response = client.get("/v1/messages?limit=ten", headers=ACME_AUTH)
    assert response.status_code == 400


def test_filter_applies_before_pagination(client, template_id):
    for _ in range(3):
        send(client, template_id, to="target@example.com")
    body = client.get(
        "/v1/messages?to=target@example.com&limit=2", headers=ACME_AUTH
    ).get_json()
    assert len(body["data"]) == 2
    assert all(m["to"] == "target@example.com" for m in body["data"])


def test_template_update_requires_if_match(client, template_id):
    response = client.patch(
        "/v1/templates/" + template_id, json={"name": "renamed"}, headers=ACME_AUTH
    )
    assert response.status_code == 428


def test_template_update_succeeds_with_current_etag(client, template_id):
    response = client.patch(
        "/v1/templates/" + template_id,
        json={"name": "renamed"},
        headers={**ACME_AUTH, "If-Match": 'W/"1"'},
    )
    assert response.status_code == 200
    assert response.get_json()["name"] == "renamed"


def test_template_update_with_stale_etag_is_409(client, template_id):
    client.patch(
        "/v1/templates/" + template_id,
        json={"name": "once"},
        headers={**ACME_AUTH, "If-Match": 'W/"1"'},
    )
    response = client.patch(
        "/v1/templates/" + template_id,
        json={"name": "twice"},
        headers={**ACME_AUTH, "If-Match": 'W/"1"'},
    )
    assert response.status_code == 409


def test_template_update_rejects_empty_values(client, template_id):
    response = client.patch(
        "/v1/templates/" + template_id,
        json={"name": "   "},
        headers={**ACME_AUTH, "If-Match": 'W/"1"'},
    )
    assert response.status_code == 400


def test_template_update_with_no_fields_is_400(client, template_id):
    response = client.patch(
        "/v1/templates/" + template_id,
        json={},
        headers={**ACME_AUTH, "If-Match": 'W/"1"'},
    )
    assert response.status_code == 400
