"""The decision action sub-resource and the appeal flow."""

import pytest


def _decide(client, auth, case_id, **body):
    payload = {"decision": "approved", "reviewer": "reviewer_kim"}
    payload.update(body)
    return client.post(
        "/v1/cases/{}/decision".format(case_id), json=payload, headers=auth
    )


def test_approve_stamps_reviewer_and_clears_reason(client, auth, case):
    response = _decide(client, auth, case["id"], decision="approved")
    assert response.status_code == 200
    body = response.get_json()
    assert body["state"] == "approved"
    assert body["decided_by"] == "reviewer_kim"
    assert body["decided_at"] is not None
    assert body["reason"] == "none"


def test_remove_requires_a_reason(client, auth, case):
    response = _decide(client, auth, case["id"], decision="removed", reason="none")
    assert response.status_code == 400
    assert response.get_json()["error"]["errors"][0]["field"] == "reason"


def test_remove_with_reason_succeeds(client, auth, case):
    response = _decide(client, auth, case["id"], decision="removed", reason="spam")
    assert response.status_code == 200
    assert response.get_json()["state"] == "removed"


def test_deciding_twice_is_409(client, auth, case):
    _decide(client, auth, case["id"], decision="approved")
    response = _decide(client, auth, case["id"], decision="removed", reason="spam")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "already_decided"


def test_decision_requires_a_reviewer(client, auth, case):
    response = client.post(
        "/v1/cases/{}/decision".format(case["id"]),
        json={"decision": "approved"},
        headers=auth,
    )
    assert response.status_code == 400


def test_invalid_decision_value_is_400(client, auth, case):
    response = _decide(client, auth, case["id"], decision="shrug")
    assert response.status_code == 400


def test_ingest_credential_cannot_decide(client, auth, ingest_auth, case):
    response = _decide(client, ingest_auth, case["id"], decision="approved")
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "insufficient_role"


def test_other_tenant_cannot_decide(client, auth, other_auth, case):
    response = _decide(client, other_auth, case["id"], decision="approved")
    assert response.status_code == 404


def test_appeal_before_decision_is_409(client, auth, case):
    response = client.post(
        "/v1/cases/{}/appeals".format(case["id"]),
        json={"submitted_by": "user_1", "statement": "unfair"},
        headers=auth,
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "not_decided"


def test_appeal_after_removal_is_201(client, auth, case):
    _decide(client, auth, case["id"], decision="removed", reason="spam")
    response = client.post(
        "/v1/cases/{}/appeals".format(case["id"]),
        json={"submitted_by": "user_1", "statement": "this is my own photo"},
        headers=auth,
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["state"] == "open"
    assert body["case_id"] == case["id"]


def test_second_open_appeal_is_409(client, auth, case):
    _decide(client, auth, case["id"], decision="removed", reason="spam")
    payload = {"submitted_by": "user_1", "statement": "unfair"}
    client.post("/v1/cases/{}/appeals".format(case["id"]), json=payload, headers=auth)
    response = client.post(
        "/v1/cases/{}/appeals".format(case["id"]), json=payload, headers=auth
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "duplicate_appeal"


def test_overturning_an_appeal_restores_the_case(client, auth, case):
    _decide(client, auth, case["id"], decision="removed", reason="spam")
    appeal = client.post(
        "/v1/cases/{}/appeals".format(case["id"]),
        json={"submitted_by": "user_1", "statement": "mine"},
        headers=auth,
    ).get_json()

    response = client.post(
        "/v1/appeals/{}/resolve".format(appeal["id"]),
        json={"outcome": "overturned", "reviewer": "reviewer_lee"},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.get_json()["state"] == "overturned"

    restored = client.get("/v1/cases/{}".format(case["id"]), headers=auth).get_json()
    assert restored["state"] == "approved"
    assert restored["reason"] == "none"


def test_upholding_an_appeal_leaves_the_case_removed(client, auth, case):
    _decide(client, auth, case["id"], decision="removed", reason="spam")
    appeal = client.post(
        "/v1/cases/{}/appeals".format(case["id"]),
        json={"submitted_by": "user_1", "statement": "mine"},
        headers=auth,
    ).get_json()

    client.post(
        "/v1/appeals/{}/resolve".format(appeal["id"]),
        json={"outcome": "upheld", "reviewer": "reviewer_lee"},
        headers=auth,
    )
    case_now = client.get("/v1/cases/{}".format(case["id"]), headers=auth).get_json()
    assert case_now["state"] == "removed"


def test_resolving_twice_is_409(client, auth, case):
    _decide(client, auth, case["id"], decision="removed", reason="spam")
    appeal = client.post(
        "/v1/cases/{}/appeals".format(case["id"]),
        json={"submitted_by": "user_1", "statement": "mine"},
        headers=auth,
    ).get_json()
    body = {"outcome": "upheld", "reviewer": "reviewer_lee"}
    client.post("/v1/appeals/{}/resolve".format(appeal["id"]), json=body, headers=auth)
    response = client.post(
        "/v1/appeals/{}/resolve".format(appeal["id"]), json=body, headers=auth
    )
    assert response.status_code == 409



def test_appeals_are_listed_under_their_case(client, auth, case):
    _decide(client, auth, case["id"], decision="removed", reason="spam")
    client.post(
        "/v1/cases/{}/appeals".format(case["id"]),
        json={"submitted_by": "user_1", "statement": "mine"},
        headers=auth,
    )
    body = client.get("/v1/cases/{}/appeals".format(case["id"]), headers=auth).get_json()
    assert len(body["data"]) == 1
    assert body["data"][0]["case_id"] == case["id"]


def test_appeals_are_tenant_isolated(client, auth, other_auth, case):
    _decide(client, auth, case["id"], decision="removed", reason="spam")
    appeal = client.post(
        "/v1/cases/{}/appeals".format(case["id"]),
        json={"submitted_by": "user_1", "statement": "mine"},
        headers=auth,
    ).get_json()
    assert client.get("/v1/appeals/{}".format(appeal["id"]), headers=other_auth).status_code == 404
