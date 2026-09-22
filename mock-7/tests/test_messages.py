"""Send, read, validation, idempotency, and ETags."""

from __future__ import annotations

from tests.conftest import ACME_AUTH, GLOBEX_AUTH, send


def test_send_returns_202_and_queues(client, template_id):
    response = send(client, template_id)
    assert response.status_code == 202
    body = response.get_json()
    assert body["status"] == "queued"
    assert body["attempts"] == 0
    assert response.headers["Location"].endswith(body["id"])


def test_send_sets_an_etag(client, template_id):
    response = send(client, template_id)
    assert response.headers["ETag"] == 'W/"1"'


def test_validation_collects_every_error(client, template_id):
    response = client.post(
        "/v1/messages",
        json={"to": "not-an-email", "subject": ""},
        headers=ACME_AUTH,
    )
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["error"]["errors"]}
    assert {"to", "subject", "template_id"} <= fields


def test_send_rejects_unknown_template(client):
    response = client.post(
        "/v1/messages",
        json={"to": "a@b.com", "subject": "Hi", "template_id": "tpl_missing"},
        headers=ACME_AUTH,
    )
    assert response.status_code == 404


def test_send_rejects_another_accounts_template(client, template_id):
    response = client.post(
        "/v1/messages",
        json={"to": "a@b.com", "subject": "Hi", "template_id": template_id},
        headers=GLOBEX_AUTH,
    )
    assert response.status_code == 404


def test_non_object_body_is_400(client):
    response = client.post("/v1/messages", json=["nope"], headers=ACME_AUTH)
    assert response.status_code == 400


def test_idempotent_replay_returns_the_same_message(client, template_id):
    headers = dict(ACME_AUTH)
    headers["Idempotency-Key"] = "abc-123"
    payload = {"to": "a@b.com", "subject": "Hi", "template_id": template_id}

    first = client.post("/v1/messages", json=payload, headers=headers)
    second = client.post("/v1/messages", json=payload, headers=headers)

    assert first.get_json()["id"] == second.get_json()["id"]
    assert second.headers["Idempotent-Replay"] == "true"


def test_without_idempotency_key_two_sends_are_distinct(client, template_id):
    first = send(client, template_id)
    second = send(client, template_id)
    assert first.get_json()["id"] != second.get_json()["id"]


def test_get_message_returns_etag(client, template_id):
    created = send(client, template_id).get_json()
    response = client.get("/v1/messages/" + created["id"], headers=ACME_AUTH)
    assert response.status_code == 200
    assert response.headers["ETag"] == 'W/"1"'


def test_get_unknown_message_is_404(client):
    response = client.get("/v1/messages/msg_nope", headers=ACME_AUTH)
    assert response.status_code == 404


def test_filter_by_status(client, template_id):
    send(client, template_id)
    response = client.get("/v1/messages?status=delivered", headers=ACME_AUTH)
    data = response.get_json()["data"]
    assert all(m["status"] == "delivered" for m in data)
    assert data


def test_filter_by_recipient(client, template_id):
    send(client, template_id, to="unique@example.com")
    response = client.get(
        "/v1/messages?to=unique@example.com", headers=ACME_AUTH
    )
    data = response.get_json()["data"]
    assert len(data) == 1
    assert data[0]["to"] == "unique@example.com"


def test_serializer_hides_internal_fields(client, template_id):
    body = send(client, template_id).get_json()
    assert "account_id" not in body
    assert "template_name" not in body
    assert "variables" not in body
