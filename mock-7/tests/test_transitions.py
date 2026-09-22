"""The delivery state machine and optimistic concurrency.

Note what is not here: `deferred`. It is a legal target in
`ALLOWED_TRANSITIONS` and nothing exercises it.
"""

from __future__ import annotations

from tests.conftest import ACME_AUTH, send


def transition(client, message_id, status, **kwargs):
    headers = dict(ACME_AUTH)
    headers.update(kwargs.pop("headers", {}))
    body = {"status": status}
    body.update(kwargs)
    return client.post(
        "/v1/messages/{}/transition".format(message_id), json=body, headers=headers
    )


def test_queued_to_sending_increments_attempts(client, template_id):
    message = send(client, template_id).get_json()
    response = transition(client, message["id"], "sending")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "sending"
    assert body["attempts"] == 1


def test_sending_to_delivered(client, template_id):
    message = send(client, template_id).get_json()
    transition(client, message["id"], "sending")
    response = transition(client, message["id"], "delivered")
    assert response.get_json()["status"] == "delivered"


def test_bounce_requires_a_reason(client, template_id):
    message = send(client, template_id).get_json()
    transition(client, message["id"], "sending")
    response = transition(client, message["id"], "bounced")
    assert response.status_code == 400
    assert response.get_json()["error"]["errors"][0]["field"] == "reason"


def test_bounce_records_the_reason(client, template_id):
    message = send(client, template_id).get_json()
    transition(client, message["id"], "sending")
    response = transition(
        client, message["id"], "bounced", reason="550 mailbox unavailable"
    )
    assert response.get_json()["last_error"] == "550 mailbox unavailable"


def test_illegal_transition_is_409(client, template_id):
    message = send(client, template_id).get_json()
    response = transition(client, message["id"], "delivered")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "invalid_transition"


def test_terminal_states_are_final(client, template_id):
    message = send(client, template_id).get_json()
    transition(client, message["id"], "sending")
    transition(client, message["id"], "delivered")
    response = transition(client, message["id"], "sending")
    assert response.status_code == 409


def test_unknown_status_is_400(client, template_id):
    message = send(client, template_id).get_json()
    response = transition(client, message["id"], "teleported")
    assert response.status_code == 400


def test_missing_status_is_400(client, template_id):
    message = send(client, template_id).get_json()
    response = client.post(
        "/v1/messages/{}/transition".format(message["id"]),
        json={},
        headers=ACME_AUTH,
    )
    assert response.status_code == 400


def test_terminal_transition_writes_an_event(client, template_id):
    message = send(client, template_id).get_json()
    transition(client, message["id"], "sending")
    transition(client, message["id"], "delivered")
    events = client.get(
        "/v1/events?message_id=" + message["id"], headers=ACME_AUTH
    ).get_json()["data"]
    assert [e["type"] for e in events] == ["message.delivered"]


def test_stale_if_match_is_409(client, template_id):
    message = send(client, template_id).get_json()
    transition(client, message["id"], "sending")
    response = transition(
        client, message["id"], "delivered", headers={"If-Match": 'W/"1"'}
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "version_conflict"


def test_current_if_match_succeeds(client, template_id):
    message = send(client, template_id).get_json()
    transition(client, message["id"], "sending")
    response = transition(
        client, message["id"], "delivered", headers={"If-Match": 'W/"2"'}
    )
    assert response.status_code == 200


def test_malformed_if_match_is_400(client, template_id):
    message = send(client, template_id).get_json()
    response = transition(
        client, message["id"], "sending", headers={"If-Match": "banana"}
    )
    assert response.status_code == 400


def test_transition_on_another_accounts_message_is_404(client, template_id):
    message = send(client, template_id).get_json()
    response = client.post(
        "/v1/messages/{}/transition".format(message["id"]),
        json={"status": "sending"},
        headers={"Authorization": "Bearer key_globex"},
    )
    assert response.status_code == 404
