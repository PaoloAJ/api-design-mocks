def _create(client, auth, service, **overrides):
    body = {"title": "Filter test", "severity": "sev3", "service_id": service["id"]}
    body.update(overrides)
    return client.post("/v1/incidents", json=body, headers=auth).get_json()


def test_filter_by_status(client, auth, service):
    a = _create(client, auth, service, title="A")
    b = _create(client, auth, service, title="B")
    client.post(
        "/v1/incidents/{}/status".format(a["id"]), json={"status": "acknowledged"}, headers=auth
    )

    response = client.get("/v1/incidents?status=acknowledged", headers=auth)
    ids = [i["id"] for i in response.get_json()["data"]]
    assert a["id"] in ids
    assert b["id"] not in ids


def test_filter_by_severity(client, auth, service):
    a = _create(client, auth, service, severity="sev1")
    b = _create(client, auth, service, severity="sev4")
    response = client.get("/v1/incidents?severity=sev1", headers=auth)
    ids = [i["id"] for i in response.get_json()["data"]]
    assert a["id"] in ids
    assert b["id"] not in ids


def test_filter_by_service(client, auth, service):
    a = _create(client, auth, service)
    response = client.get(
        "/v1/incidents?service_id={}".format(service["id"]), headers=auth
    )
    ids = [i["id"] for i in response.get_json()["data"]]
    assert a["id"] in ids


def test_filter_by_tag(client, auth, service):
    a = _create(client, auth, service, tags=["env:prod", "team:api"])
    b = _create(client, auth, service, tags=["env:staging"])
    response = client.get("/v1/incidents?tag=env:prod", headers=auth)
    ids = [i["id"] for i in response.get_json()["data"]]
    assert a["id"] in ids
    assert b["id"] not in ids


def test_search_q(client, auth, service):
    a = _create(client, auth, service, title="Checkout is on fire")
    b = _create(client, auth, service, title="Unrelated")
    response = client.get("/v1/incidents?q=fire", headers=auth)
    ids = [i["id"] for i in response.get_json()["data"]]
    assert a["id"] in ids
    assert b["id"] not in ids


def test_invalid_status_filter_is_400(client, auth):
    response = client.get("/v1/incidents?status=nope", headers=auth)
    assert response.status_code == 400


def test_pagination_cursor_walks_all_records(client, auth, service):
    created = [
        _create(client, auth, service, title="Incident {}".format(i)) for i in range(5)
    ]
    seen = []
    cursor = None
    while True:
        url = "/v1/incidents?limit=2"
        if cursor:
            url += "&cursor={}".format(cursor)
        response = client.get(url, headers=auth)
        body = response.get_json()
        seen.extend(i["id"] for i in body["data"])
        if not body["pagination"]["has_more"]:
            break
        cursor = body["pagination"]["next_cursor"]

    assert set(seen) == {c["id"] for c in created}
    assert len(seen) == len(created)


def test_limit_is_clamped_not_rejected(client, auth, service):
    _create(client, auth, service)
    response = client.get("/v1/incidents?limit=10000", headers=auth)
    assert response.status_code == 200
    assert response.get_json()["pagination"]["limit"] == 100


def test_tenant_isolation_on_list(client, auth, other_auth, service):
    _create(client, auth, service)
    response = client.get("/v1/incidents", headers=other_auth)
    assert response.get_json()["data"] == []
