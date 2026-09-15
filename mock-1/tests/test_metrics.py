"""Bulk ingest: 202, partial success, and payload limits."""

import time


def _point(value=1.0, offset=0):
    return [time.time() - offset, value]


def test_valid_batch_returns_202(client, auth):
    response = client.post(
        "/v1/series",
        json={"series": [{"metric": "api.latency", "points": [_point(12.5)], "tags": ["env:prod"]}]},
        headers=auth,
    )
    assert response.status_code == 202
    assert response.get_json() == {"accepted": 1, "rejected": 0, "errors": []}


def test_partial_success_reports_failed_indexes(client, auth):
    response = client.post(
        "/v1/series",
        json={
            "series": [
                {"metric": "good.metric", "points": [_point()]},
                {"metric": "", "points": [_point()]},
                {"metric": "also.good", "points": [_point()]},
            ]
        },
        headers=auth,
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["accepted"] == 2
    assert body["rejected"] == 1
    assert body["errors"][0]["index"] == 1


def test_all_invalid_returns_400(client, auth):
    response = client.post(
        "/v1/series", json={"series": [{"metric": "x"}]}, headers=auth
    )
    assert response.status_code == 400
    assert response.get_json()["accepted"] == 0


def test_empty_series_is_400(client, auth):
    assert client.post("/v1/series", json={"series": []}, headers=auth).status_code == 400


def test_oversized_batch_is_413(client, auth):
    series = [{"metric": "m", "points": [_point()]} for _ in range(1001)]
    response = client.post("/v1/series", json={"series": series}, headers=auth)
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "payload_too_large"


def test_future_timestamp_is_rejected(client, auth):
    response = client.post(
        "/v1/series",
        json={"series": [{"metric": "m", "points": [[time.time() + 99999, 1]]}]},
        headers=auth,
    )
    assert response.status_code == 400


def test_malformed_tag_is_rejected(client, auth):
    response = client.post(
        "/v1/series",
        json={"series": [{"metric": "m", "points": [_point()], "tags": ["notakeyvalue"]}]},
        headers=auth,
    )
    assert response.status_code == 400
