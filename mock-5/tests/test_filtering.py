"""Collection filtering: AND semantics and validation."""

import pytest


@pytest.fixture
def fixtures(client, auth):
    specs = [
        (1000, "USD", "cus_amir", "Pro plan renewal"),
        (2000, "USD", "cus_amir", "Add-on seats"),
        (3000, "EUR", "cus_bree", "Consulting hours"),
    ]
    for amount, currency, customer_id, description in specs:
        client.post(
            "/v1/payments",
            json={
                "amount": amount,
                "currency": currency,
                "customer_id": customer_id,
                "description": description,
            },
            headers=auth,
        )


def test_filter_by_currency(client, auth, fixtures):
    body = client.get("/v1/payments?currency=USD", headers=auth).get_json()
    assert len(body["data"]) == 2


def test_filter_by_customer_id(client, auth, fixtures):
    body = client.get("/v1/payments?customer_id=cus_amir", headers=auth).get_json()
    assert len(body["data"]) == 2


def test_filter_by_status(client, auth, fixtures):
    body = client.get("/v1/payments?status=authorized", headers=auth).get_json()
    assert len(body["data"]) == 3
    body = client.get("/v1/payments?status=captured", headers=auth).get_json()
    assert len(body["data"]) == 0


def test_free_text_search(client, auth, fixtures):
    body = client.get("/v1/payments?q=consulting", headers=auth).get_json()
    assert len(body["data"]) == 1


def test_filters_are_anded(client, auth, fixtures):
    body = client.get(
        "/v1/payments?currency=USD&customer_id=cus_amir", headers=auth
    ).get_json()
    assert len(body["data"]) == 2

    body = client.get(
        "/v1/payments?currency=EUR&customer_id=cus_amir", headers=auth
    ).get_json()
    assert len(body["data"]) == 0


def test_invalid_filter_value_is_400(client, auth, fixtures):
    assert client.get("/v1/payments?currency=zzz", headers=auth).status_code == 400
    assert client.get("/v1/payments?status=bogus", headers=auth).status_code == 400


def test_tenant_isolation_on_list(client, auth, other_auth, fixtures):
    mine = client.get("/v1/payments", headers=auth).get_json()
    theirs = client.get("/v1/payments", headers=other_auth).get_json()
    assert len(mine["data"]) == 3
    assert len(theirs["data"]) == 0
