"""Bulk ingest: 202, partial success, per-item errors."""

import time


def _event(**overrides):
    event = {"build_id": "bld_abc", "kind": "job_started", "timestamp": time.time()}
    event.update(overrides)
    return event


def test_valid_batch_returns_202(client):
    response = client.post("/v1/events", json={"events": [_event(), _event()]})
    assert response.status_code == 202
    assert response.get_json() == {"accepted": 2, "rejected": 0, "errors": []}


def test_partial_success_reports_the_index(client):
    response = client.post(
        "/v1/events",
        json={"events": [_event(), _event(kind="nonsense"), _event()]},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["accepted"] == 2
    assert body["errors"][0]["index"] == 1


def test_batch_with_nothing_valid_is_400(client):
    response = client.post("/v1/events", json={"events": [_event(kind="nope")]})
    assert response.status_code == 400
    assert response.get_json()["accepted"] == 0


def test_empty_batch_is_400(client):
    assert client.post("/v1/events", json={"events": []}).status_code == 400


def test_missing_events_key_is_400(client):
    assert client.post("/v1/events", json={}).status_code == 400


def test_oversized_batch_is_413(client):
    response = client.post("/v1/events", json={"events": [_event()] * 501})
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "payload_too_large"


def test_future_timestamp_is_rejected(client):
    response = client.post(
        "/v1/events", json={"events": [_event(timestamp=time.time() + 9999)]}
    )
    assert response.status_code == 400
    assert "future" in response.get_json()["errors"][0]["reason"]


def test_ancient_timestamp_is_rejected(client):
    response = client.post(
        "/v1/events", json={"events": [_event(timestamp=time.time() - 99999)]}
    )
    assert response.status_code == 400


def test_boolean_timestamp_is_rejected(client):
    response = client.post("/v1/events", json={"events": [_event(timestamp=True)]})
    assert response.status_code == 400


def test_missing_build_id_is_rejected(client):
    response = client.post("/v1/events", json={"events": [_event(build_id="")]})
    assert response.status_code == 400
