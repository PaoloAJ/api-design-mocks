"""Middleware, auth, tenant isolation, and the error envelope."""

from __future__ import annotations

from tests.conftest import ACME_AUTH, GLOBEX_AUTH


def test_healthz_needs_no_auth(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_missing_auth_is_401(client):
    response = client.get("/v1/messages")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "unauthorized"


def test_malformed_auth_scheme_is_401(client):
    response = client.get("/v1/messages", headers={"Authorization": "key_acme"})
    assert response.status_code == 401


def test_unknown_key_is_401(client):
    response = client.get("/v1/messages", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_request_id_is_echoed(client):
    response = client.get("/healthz", headers={"X-Request-ID": "trace-123"})
    assert response.headers["X-Request-ID"] == "trace-123"


def test_request_id_is_minted_when_absent(client):
    response = client.get("/healthz")
    assert response.headers.get("X-Request-ID")


def test_rate_limit_headers_present_for_authenticated_calls(client):
    response = client.get("/v1/messages", headers=ACME_AUTH)
    assert response.headers["X-RateLimit-Limit"] == "120"
    assert int(response.headers["X-RateLimit-Remaining"]) < 120


def test_unknown_route_returns_json_envelope(client):
    response = client.get("/v1/nope", headers=ACME_AUTH)
    assert response.status_code == 404
    assert "error" in response.get_json()


def test_wrong_method_returns_json_envelope(client):
    response = client.delete("/v1/messages", headers=ACME_AUTH)
    assert response.status_code == 405
    assert "error" in response.get_json()


def test_tenant_cannot_read_another_accounts_message(client):
    acme = client.get("/v1/messages", headers=ACME_AUTH).get_json()["data"]
    target = acme[0]["id"]
    response = client.get("/v1/messages/" + target, headers=GLOBEX_AUTH)
    assert response.status_code == 404


def test_list_is_scoped_to_the_caller(client):
    acme = client.get("/v1/messages", headers=ACME_AUTH).get_json()["data"]
    globex = client.get("/v1/messages", headers=GLOBEX_AUTH).get_json()["data"]
    assert {m["to"] for m in acme}.isdisjoint({m["to"] for m in globex})
