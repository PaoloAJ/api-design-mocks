"""Bulk ingest: partial success, per-item errors, and pagination."""

from __future__ import annotations

from tests.conftest import ACME_AUTH, GLOBEX_AUTH, send


def test_ingest_accepts_a_batch(client, template_id):
    message = send(client, template_id).get_json()
    response = client.post(
        "/v1/events",
        json={
            "events": [
                {"message_id": message["id"], "type": "message.delivered"},
                {"message_id": message["id"], "type": "message.deferred"},
            ]
        },
        headers=ACME_AUTH,
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["accepted"] == 2
    assert body["rejected"] == 0


def test_partial_success_reports_the_failing_index(client, template_id):
    message = send(client, template_id).get_json()
    response = client.post(
        "/v1/events",
        json={
            "events": [
                {"message_id": message["id"], "type": "message.delivered"},
                {"type": "message.delivered"},
                {"message_id": "msg_nope", "type": "message.delivered"},
                {"message_id": message["id"], "type": "nonsense"},
            ]
        },
        headers=ACME_AUTH,
    )
    body = response.get_json()
    assert body["accepted"] == 1
    assert body["rejected"] == 3
    assert [e["index"] for e in body["errors"]] == [1, 2, 3]


def test_ingest_rejects_another_accounts_message(client, template_id):
    message = send(client, template_id).get_json()
    response = client.post(
        "/v1/events",
        json={"events": [{"message_id": message["id"], "type": "message.delivered"}]},
        headers=GLOBEX_AUTH,
    )
    body = response.get_json()
    assert body["accepted"] == 0
    assert body["errors"][0]["code"] == "not_found"


def test_empty_batch_is_400(client):
    response = client.post("/v1/events", json={"events": []}, headers=ACME_AUTH)
    assert response.status_code == 400


def test_missing_events_array_is_400(client):
    response = client.post("/v1/events", json={}, headers=ACME_AUTH)
    assert response.status_code == 400


def test_oversized_batch_is_400(client, template_id):
    message = send(client, template_id).get_json()
    response = client.post(
        "/v1/events",
        json={
            "events": [
                {"message_id": message["id"], "type": "message.delivered"}
            ]
            * 101
        },
        headers=ACME_AUTH,
    )
    assert response.status_code == 400


def test_ingest_does_not_move_the_message(client, template_id):
    """The deliberate gap: an event is recorded, the message is untouched."""
    message = send(client, template_id).get_json()
    client.post(
        "/v1/events",
        json={"events": [{"message_id": message["id"], "type": "message.delivered"}]},
        headers=ACME_AUTH,
    )
    after = client.get("/v1/messages/" + message["id"], headers=ACME_AUTH).get_json()
    assert after["status"] == "queued"
