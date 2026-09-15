def test_list_services_scoped_to_org(client, auth, service):
    response = client.get("/v1/services", headers=auth)
    assert response.status_code == 200
    ids = [s["id"] for s in response.get_json()["data"]]
    assert service["id"] in ids


def test_list_services_does_not_leak_other_org(client, other_auth, service):
    response = client.get("/v1/services", headers=other_auth)
    ids = [s["id"] for s in response.get_json()["data"]]
    assert service["id"] not in ids


def test_get_service(client, auth, service):
    response = client.get("/v1/services/{}".format(service["id"]), headers=auth)
    assert response.status_code == 200
    assert response.get_json()["id"] == service["id"]


def test_get_service_other_org_is_404(client, other_auth, service):
    response = client.get("/v1/services/{}".format(service["id"]), headers=other_auth)
    assert response.status_code == 404


def test_get_missing_service_is_404(client, auth):
    response = client.get("/v1/services/svc_doesnotexist", headers=auth)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_services_require_auth(client):
    response = client.get("/v1/services")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "missing_api_key"


def test_no_post_route_for_services(client, auth):
    response = client.post("/v1/services", json={"name": "new-svc"}, headers=auth)
    assert response.status_code == 405
