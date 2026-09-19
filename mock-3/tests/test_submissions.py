"""Bulk ingest on /v1/submissions — partial success and the 202 boundary."""


def _item(external_id="c1", **over):
    item = {"external_id": external_id, "kind": "post", "text": "some content"}
    item.update(over)
    return item


def test_valid_batch_returns_202(client, auth):
    response = client.post(
        "/v1/submissions", json={"items": [_item("a"), _item("b")]}, headers=auth
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["accepted"] == 2
    assert body["rejected"] == 0


def test_partial_success_reports_index_per_error(client, auth):
    response = client.post(
        "/v1/submissions",
        json={"items": [_item("a"), _item("b", kind="hologram"), _item("c")]},
        headers=auth,
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["accepted"] == 2
    assert body["rejected"] == 1
    assert body["errors"][0]["index"] == 1


def test_all_items_invalid_is_400(client, auth):
    response = client.post(
        "/v1/submissions", json={"items": [_item("a", text="")]}, headers=auth
    )
    assert response.status_code == 400
    assert response.get_json()["accepted"] == 0


def test_empty_batch_is_400(client, auth):
    assert client.post("/v1/submissions", json={"items": []}, headers=auth).status_code == 400



def test_oversized_batch_is_413(client, auth):
    items = [_item("c{}".format(i)) for i in range(501)]
    response = client.post("/v1/submissions", json={"items": items}, headers=auth)
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "payload_too_large"


def test_resubmitting_the_same_content_is_not_an_error(client, auth):
    client.post("/v1/submissions", json={"items": [_item("dup")]}, headers=auth)
    response = client.post("/v1/submissions", json={"items": [_item("dup")]}, headers=auth)
    assert response.status_code == 202
    assert response.get_json()["items"][0]["duplicate"] is True


def test_ingest_credential_may_submit(client, ingest_auth):
    response = client.post(
        "/v1/submissions", json={"items": [_item("a")]}, headers=ingest_auth
    )
    assert response.status_code == 202


def test_submissions_require_auth(client):
    assert client.post("/v1/submissions", json={"items": [_item()]}).status_code == 401
