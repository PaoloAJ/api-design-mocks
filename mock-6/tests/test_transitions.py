"""The build state machine and its side effects on jobs."""


def _transition(client, build_id, state):
    return client.post(
        "/v1/projects/web/builds/{}/transition".format(build_id),
        json={"state": state},
    )


def test_running_stamps_started_at_and_opens_jobs(client, build):
    response = _transition(client, build["id"], "running")
    assert response.status_code == 200
    assert response.get_json()["state"] == "running"
    assert response.get_json()["started_at"] is not None

    jobs = client.get(
        "/v1/projects/web/builds/{}/jobs".format(build["id"])
    ).get_json()["data"]
    assert sorted(j["name"] for j in jobs) == ["build", "test"]
    assert {j["state"] for j in jobs} == {"running"}


def test_passed_stamps_finished_at_and_closes_jobs(client, build):
    _transition(client, build["id"], "running")
    response = _transition(client, build["id"], "passed")
    assert response.status_code == 200
    assert response.get_json()["finished_at"] is not None

    jobs = client.get(
        "/v1/projects/web/builds/{}/jobs".format(build["id"])
    ).get_json()["data"]
    assert {j["state"] for j in jobs} == {"passed"}


def test_failed_closes_jobs_as_failed(client, build):
    _transition(client, build["id"], "running")
    _transition(client, build["id"], "failed")
    jobs = client.get(
        "/v1/projects/web/builds/{}/jobs".format(build["id"])
    ).get_json()["data"]
    assert {j["state"] for j in jobs} == {"failed"}


def test_repeat_transition_is_a_no_op(client, build):
    _transition(client, build["id"], "running")
    before = client.get(
        "/v1/projects/web/builds/{}".format(build["id"])
    ).get_json()
    _transition(client, build["id"], "running")
    after = client.get("/v1/projects/web/builds/{}".format(build["id"])).get_json()
    assert before["version"] == after["version"]
    assert before["started_at"] == after["started_at"]

    jobs = client.get(
        "/v1/projects/web/builds/{}/jobs".format(build["id"])
    ).get_json()["data"]
    assert len(jobs) == 2  # not four


def test_terminal_state_cannot_transition_again(client, build):
    _transition(client, build["id"], "running")
    _transition(client, build["id"], "passed")
    response = _transition(client, build["id"], "failed")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "invalid_state_transition"


def test_unknown_target_state_is_400(client, build):
    assert _transition(client, build["id"], "exploded").status_code == 400


def test_transition_on_another_projects_build_is_404(client, build):
    response = client.post(
        "/v1/projects/api/builds/{}/transition".format(build["id"]),
        json={"state": "running"},
    )
    assert response.status_code == 404
