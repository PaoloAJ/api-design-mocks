"""Middleware, error envelope, and the endpoints that are not a resource."""


def test_healthz_needs_no_project(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_root_lists_the_endpoints(client):
    body = client.get("/").get_json()
    assert "builds" in body["endpoints"]


def test_request_id_is_echoed_when_supplied(client):
    response = client.get("/healthz", headers={"X-Request-ID": "trace-me"})
    assert response.headers["X-Request-ID"] == "trace-me"


def test_request_id_is_minted_when_absent(client):
    assert client.get("/").headers["X-Request-ID"]


def test_timing_header_is_attached(client):
    assert "X-Response-Time-Ms" in client.get("/").headers


def test_rate_limit_headers_are_attached(client):
    response = client.get("/v1/projects/web/builds")
    assert response.headers["X-RateLimit-Limit"] == "120"
    assert int(response.headers["X-RateLimit-Remaining"]) < 120


def test_unknown_route_returns_json_not_html(client):
    response = client.get("/v1/nope")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_job_detail_is_scoped_to_the_project(client, build):
    client.post(
        "/v1/projects/web/builds/{}/transition".format(build["id"]),
        json={"state": "running"},
    )
    jobs = client.get(
        "/v1/projects/web/builds/{}/jobs".format(build["id"])
    ).get_json()["data"]
    job_id = jobs[0]["id"]
    assert client.get("/v1/projects/web/jobs/{}".format(job_id)).status_code == 200
    assert client.get("/v1/projects/api/jobs/{}".format(job_id)).status_code == 404


def test_jobs_list_is_empty_before_the_build_starts(client, build):
    body = client.get(
        "/v1/projects/web/builds/{}/jobs".format(build["id"])
    ).get_json()
    assert body["data"] == []
