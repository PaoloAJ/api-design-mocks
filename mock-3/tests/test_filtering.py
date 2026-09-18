"""Collection filters on /v1/cases."""

import pytest


@pytest.fixture
def population(client, auth):
    specs = [
        ("post_a", "post", "spam", "priority", ["lang:en"]),
        ("post_b", "comment", "harassment", "standard", ["lang:en", "sev:high"]),
        ("post_c", "image", "nudity", "legal", ["lang:de"]),
    ]
    for external_id, kind, reason, queue, labels in specs:
        client.post(
            "/v1/cases",
            json={
                "external_id": external_id,
                "kind": kind,
                "text": "content for {}".format(external_id),
                "reason": reason,
                "queue": queue,
                "labels": labels,
            },
            headers=auth,
        )


def _ids(response):
    return [c["external_id"] for c in response.get_json()["data"]]


def test_filter_by_queue(client, auth, population):
    assert _ids(client.get("/v1/cases?queue=legal", headers=auth)) == ["post_c"]


def test_filter_by_kind(client, auth, population):
    assert _ids(client.get("/v1/cases?kind=comment", headers=auth)) == ["post_b"]



def test_labels_are_anded(client, auth, population):
    response = client.get("/v1/cases?label=lang:en&label=sev:high", headers=auth)
    assert _ids(response) == ["post_b"]


def test_free_text_search_matches_body(client, auth, population):
    assert _ids(client.get("/v1/cases?q=post_c", headers=auth)) == ["post_c"]


def test_invalid_queue_filter_is_400(client, auth, population):
    assert client.get("/v1/cases?queue=nope", headers=auth).status_code == 400



def test_filters_do_not_cross_tenants(client, auth, other_auth, population):
    assert client.get("/v1/cases", headers=other_auth).get_json()["data"] == []
