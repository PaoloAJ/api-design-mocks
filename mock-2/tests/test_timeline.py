def test_list_timeline_includes_creation_entry(client, auth, incident):
    response = client.get("/v1/incidents/{}/timeline".format(incident["id"]), headers=auth)
    assert response.status_code == 200
    kinds = [e["kind"] for e in response.get_json()["data"]]
    assert "created" in kinds


def test_post_timeline_note(client, auth, incident):
    response = client.post(
        "/v1/incidents/{}/timeline".format(incident["id"]),
        json={"author": "priya", "message": "Rolled back the deploy."},
        headers=auth,
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["kind"] == "note"
    assert body["author"] == "priya"


def test_post_timeline_note_requires_fields(client, auth, incident):
    response = client.post(
        "/v1/incidents/{}/timeline".format(incident["id"]),
        json={"author": "priya"},
        headers=auth,
    )
    assert response.status_code == 400


def test_timeline_note_allowed_on_resolved_incident(client, auth, incident):
    iid = incident["id"]
    for status in ("acknowledged", "resolved"):
        client.post("/v1/incidents/{}/status".format(iid), json={"status": status}, headers=auth)
    response = client.post(
        "/v1/incidents/{}/timeline".format(iid),
        json={"author": "priya", "message": "Postmortem notes here."},
        headers=auth,
    )
    assert response.status_code == 201


def test_timeline_entry_has_no_mutation_route(client, auth, incident):
    """No PATCH/DELETE exists for an individual timeline entry -- it is an
    append-only audit log, not an editable resource."""
    listing = client.get(
        "/v1/incidents/{}/timeline".format(incident["id"]), headers=auth
    )
    entry_id = listing.get_json()["data"][0]["id"]
    response = client.patch(
        "/v1/incidents/{}/timeline/{}".format(incident["id"], entry_id),
        json={"message": "edited"},
        headers=auth,
    )
    assert response.status_code == 404


def test_timeline_other_org_is_404(client, other_auth, incident):
    response = client.get(
        "/v1/incidents/{}/timeline".format(incident["id"]), headers=other_auth
    )
    assert response.status_code == 404
