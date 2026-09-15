"""Collection filtering: AND semantics, repeated params, validation."""

import pytest


@pytest.fixture
def fixtures(client, auth):
    specs = [
        ("Prod API", "metric", ["env:prod", "team:api"]),
        ("Prod Checkout", "log", ["env:prod", "team:checkout"]),
        ("Staging API", "metric", ["env:staging", "team:api"]),
    ]
    for name, mtype, tags in specs:
        client.post(
            "/v1/monitors",
            json={"name": name, "type": mtype, "query": "q", "tags": tags},
            headers=auth,
        )


def test_filter_by_type(client, auth, fixtures):
    body = client.get("/v1/monitors?type=metric", headers=auth).get_json()
    assert len(body["data"]) == 2


def test_repeated_tag_params_are_anded(client, auth, fixtures):
    body = client.get(
        "/v1/monitors?tag=env:prod&tag=team:api", headers=auth
    ).get_json()
    assert len(body["data"]) == 1
    assert body["data"][0]["name"] == "Prod API"


def test_tag_filter_is_case_insensitive(client, auth, fixtures):
    body = client.get("/v1/monitors?tag=ENV:PROD", headers=auth).get_json()
    assert len(body["data"]) == 2


def test_free_text_search(client, auth, fixtures):
    body = client.get("/v1/monitors?q=checkout", headers=auth).get_json()
    assert len(body["data"]) == 1


def test_invalid_filter_value_is_400(client, auth, fixtures):
    assert client.get("/v1/monitors?type=carrier-pigeon", headers=auth).status_code == 400
    assert client.get("/v1/monitors?status=bogus", headers=auth).status_code == 400
    assert client.get("/v1/monitors?enabled=maybe", headers=auth).status_code == 400


def test_tags_are_normalized_and_deduped(client, auth):
    body = client.post(
        "/v1/monitors",
        json={
            "name": "Tagged",
            "type": "metric",
            "query": "q",
            "tags": ["Env:Prod", "env:prod", " team:api "],
        },
        headers=auth,
    ).get_json()
    assert body["tags"] == ["env:prod", "team:api"]


def test_too_many_tags_is_400(client, auth):
    response = client.post(
        "/v1/monitors",
        json={
            "name": "Too many",
            "type": "metric",
            "query": "q",
            "tags": ["k{}:v".format(i) for i in range(25)],
        },
        headers=auth,
    )
    assert response.status_code == 400
