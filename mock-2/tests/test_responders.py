def test_add_responder(client, auth, incident):
    response = client.post(
        "/v1/incidents/{}/responders".format(incident["id"]),
        json={"handle": "ada"},
        headers=auth,
    )
    assert response.status_code == 200
    assert "ada" in response.get_json()["responders"]


def test_add_responder_is_idempotent(client, auth, incident):
    iid = incident["id"]
    first = client.post(
        "/v1/incidents/{}/responders".format(iid), json={"handle": "ada"}, headers=auth
    )
    second = client.post(
        "/v1/incidents/{}/responders".format(iid), json={"handle": "ada"}, headers=auth
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["responders"].count("ada") == 1
    assert second.get_json()["version"] == first.get_json()["version"]


def test_add_responder_requires_handle(client, auth, incident):
    response = client.post(
        "/v1/incidents/{}/responders".format(incident["id"]), json={}, headers=auth
    )
    assert response.status_code == 400


def test_remove_responder(client, auth, incident):
    iid = incident["id"]
    client.post("/v1/incidents/{}/responders".format(iid), json={"handle": "ada"}, headers=auth)
    response = client.delete("/v1/incidents/{}/responders/ada".format(iid), headers=auth)
    assert response.status_code == 204
    updated = client.get("/v1/incidents/{}".format(iid), headers=auth).get_json()
    assert "ada" not in updated["responders"]


def test_remove_responder_not_present_is_404(client, auth, incident):
    response = client.delete(
        "/v1/incidents/{}/responders/nobody".format(incident["id"]), headers=auth
    )
    assert response.status_code == 404


def test_add_responder_other_org_is_404(client, other_auth, incident):
    response = client.post(
        "/v1/incidents/{}/responders".format(incident["id"]),
        json={"handle": "ada"},
        headers=other_auth,
    )
    assert response.status_code == 404


def test_remove_responder_writes_timeline_entry(client, auth, incident):
    iid = incident["id"]
    client.post("/v1/incidents/{}/responders".format(iid), json={"handle": "ada"}, headers=auth)
    client.delete("/v1/incidents/{}/responders/ada".format(iid), headers=auth)
    timeline = client.get("/v1/incidents/{}/timeline".format(iid), headers=auth).get_json()
    kinds = [e["kind"] for e in timeline["data"]]
    assert "responder_added" in kinds
    assert "responder_removed" in kinds
