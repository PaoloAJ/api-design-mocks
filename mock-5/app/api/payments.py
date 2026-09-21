"""/v1/payments — the main CRUD resource.

Shows the pieces an API-design interview usually probes: correct verbs and
status codes, filtering + cursor pagination on the collection, optimistic
concurrency with ETag/If-Match, idempotent creates, and a sub-resource for a
state transition that is not a plain field write.

There is no DELETE here. A payment is a financial record; once money has been
held or moved, deleting the row would erase the audit trail. `void` and
`refund` are the two ways to undo one, and both leave a record behind.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.pagination import paginate, parse_limit
from app.core.store import store, utcnow
from app.core.validation import (
    CURRENCIES,
    PAYMENT_STATUSES,
    validate_payment_create,
    validate_payment_patch,
    validate_refund_request,
    validate_status_transition,
)

bp = Blueprint("payments", __name__, url_prefix="/v1/payments")

# Terminal: nothing further can happen to a payment in one of these states
# through the manual status action. `disputed` is deliberately absent from
# this set — see the transition handler below.
TERMINAL_STATUSES = {"voided", "settled", "failed"}


def serialize(payment):
    """Shape the wire representation.

    Kept separate from storage so internal fields can exist without leaking,
    and so a v2 representation can be added without touching the store.
    """
    return {
        "id": payment["id"],
        "amount": payment["amount"],
        "currency": payment["currency"],
        "customer_id": payment["customer_id"],
        "description": payment["description"],
        "metadata": payment["metadata"],
        "status": payment["status"],
        "amount_refunded": payment["amount_refunded"],
        "refundable_amount": payment["amount"] - payment["amount_refunded"],
        "captured_at": payment["captured_at"],
        "voided_at": payment["voided_at"],
        "settled_at": payment["settled_at"],
        "settlement_reference": payment["settlement_reference"],
        "org_id": payment["org_id"],
        "version": payment["version"],
        "created_at": payment["created_at"],
        "updated_at": payment["updated_at"],
        # Hypermedia-lite: tells a client where the related refunds live
        # without making them build URLs by string concatenation.
        "links": {
            "self": "/v1/payments/{}".format(payment["id"]),
            "refunds": "/v1/payments/{}/refunds".format(payment["id"]),
        },
    }


def etag_for(payment):
    """Weak ETag derived from the version counter.

    Weak (`W/`) because it marks semantic equivalence, not byte equality —
    two responses with the same version are interchangeable even if key order
    or whitespace differs.
    """
    return 'W/"{}-{}"'.format(payment["id"], payment["version"])


def _parse_if_match(header):
    """Extract the version integer from an `If-Match` ETag.

    `*` means "any existing version" — the client only asserts the resource
    exists, so no version check is performed.
    """
    if not header or header.strip() == "*":
        return None
    value = header.strip()
    if value.startswith("W/"):
        value = value[2:]
    value = value.strip('"')
    try:
        return int(value.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        raise ValidationError(
            "Malformed If-Match header; use the ETag from a previous response.",
            errors=[{"field": "If-Match", "message": "malformed etag"}],
        )


@bp.get("")
def list_payments():
    """GET /v1/payments — filter, then paginate.

    Filters are AND-ed. Filtering must run before `paginate()` — filtering a
    page after it has already been sliced produces short pages and a
    `has_more` that lies.
    """
    records = [p for p in store.list("payments") if p["org_id"] == g.org_id]

    status = request.args.get("status")
    if status:
        if status not in PAYMENT_STATUSES:
            raise ValidationError(
                "Invalid status filter.",
                errors=[
                    {
                        "field": "status",
                        "message": "must be one of: {}".format(
                            ", ".join(sorted(PAYMENT_STATUSES))
                        ),
                    }
                ],
            )
        records = [p for p in records if p["status"] == status]

    currency = request.args.get("currency")
    if currency:
        if currency.upper() not in CURRENCIES:
            raise ValidationError(
                "Invalid currency filter.",
                errors=[
                    {
                        "field": "currency",
                        "message": "must be one of: {}".format(", ".join(sorted(CURRENCIES))),
                    }
                ],
            )
        records = [p for p in records if p["currency"] == currency.upper()]

    customer_id = request.args.get("customer_id")
    if customer_id:
        records = [p for p in records if p["customer_id"] == customer_id]

    # Substring match stands in for full-text search. At scale this belongs in
    # a search index, not a table scan.
    query = request.args.get("q")
    if query:
        needle = query.lower()
        records = [
            p
            for p in records
            if needle in p["description"].lower() or needle in p["customer_id"].lower()
        ]

    limit = parse_limit(request.args.get("limit"))
    page, next_cursor = paginate(
        records, limit=limit, cursor=request.args.get("cursor")
    )

    return jsonify(
        {
            "data": [serialize(p) for p in page],
            "pagination": {
                "limit": limit,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
        }
    )


@bp.post("")
def create_payment():
    """POST /v1/payments — 201 with Location, idempotent on Idempotency-Key.

    POST is not naturally idempotent, so a client that times out cannot safely
    retry — retrying blind risks authorizing the same card twice. The
    `Idempotency-Key` header fixes that: a repeat of the same key returns the
    original resource instead of creating a second payment.
    """
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing = store.lookup_idempotency(idempotency_key)
        if existing:
            kind, resource_id = existing
            payment = store.get(kind, resource_id)
            response = jsonify(serialize(payment))
            response.status_code = 200  # 200, not 201 — nothing was created.
            response.headers["Location"] = "/v1/payments/{}".format(payment["id"])
            response.headers["ETag"] = etag_for(payment)
            response.headers["Idempotent-Replay"] = "true"
            return response

    payload = validate_payment_create(request.get_json(silent=True))
    payload["org_id"] = g.org_id

    payment = store.create("payments", payload)
    if idempotency_key:
        store.remember_idempotency(idempotency_key, "payments", payment["id"])

    response = jsonify(serialize(payment))
    response.status_code = 201
    response.headers["Location"] = "/v1/payments/{}".format(payment["id"])
    response.headers["ETag"] = etag_for(payment)
    return response


@bp.get("/<payment_id>")
def get_payment(payment_id):
    """GET one — supports conditional requests via If-None-Match (304)."""
    payment = _get_owned(payment_id)
    etag = etag_for(payment)
    if request.headers.get("If-None-Match") == etag:
        # Saves the client re-downloading a body it already has.
        return "", 304, {"ETag": etag}
    response = jsonify(serialize(payment))
    response.headers["ETag"] = etag
    return response


@bp.patch("/<payment_id>")
def patch_payment(payment_id):
    """PATCH — partial update, optionally guarded by If-Match.

    Only `description` and `metadata` are mutable. `amount` and `currency`
    describe money that has already been authorized; PATCH is for correcting
    bookkeeping labels, not rewriting what happened.
    """
    payment = _get_owned(payment_id)
    expected_version = _parse_if_match(request.headers.get("If-Match"))
    changes = validate_payment_patch(request.get_json(silent=True), payment)

    updated = store.update(
        "payments", payment_id, changes, expected_version=expected_version
    )
    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


@bp.post("/<payment_id>/status")
def transition_status(payment_id):
    """POST a state transition as a sub-resource.

    A status change is an *event* with side effects (capturing sets
    `captured_at`, voiding sets `voided_at`), not a field assignment. Modeling
    it as its own endpoint keeps PATCH free of hidden behavior.

    `settled` and `failed` cannot be set here at all — those are reported by
    the settlement processor via `POST /v1/settlements`, never by a client
    calling this endpoint directly.
    """
    payment = _get_owned(payment_id)
    new_status = validate_status_transition(request.get_json(silent=True))
    previous = payment["status"]

    if previous == new_status:
        # No-op transitions must not re-run side effects.
        response = jsonify(serialize(payment))
        response.headers["ETag"] = etag_for(payment)
        return response

    if previous in TERMINAL_STATUSES:
        raise ConflictError(
            "Payment '{}' is {} and cannot transition further.".format(
                payment_id, previous
            ),
            code="terminal_state",
        )

    if new_status == "captured":
        if previous != "authorized":
            raise ConflictError(
                "Only an authorized payment can be captured.",
                code="invalid_state_transition",
            )
        updated = store.update(
            "payments", payment_id, {"status": "captured", "captured_at": utcnow()}
        )
    elif new_status == "voided":
        if previous != "authorized":
            raise ConflictError(
                "Only an authorized payment can be voided.",
                code="invalid_state_transition",
            )
        updated = store.update(
            "payments", payment_id, {"status": "voided", "voided_at": utcnow()}
        )
    elif new_status == "authorized":
        raise ConflictError(
            "A payment cannot be moved back to authorized.",
            code="invalid_state_transition",
        )
    elif new_status in ("settled", "failed"):
        raise ConflictError(
            "Settlement outcomes are reported by POST /v1/settlements, not set directly.",
            code="settlement_is_automated",
        )
    else:
        # new_status == "disputed": reachable from "authorized" or "captured"
        # so support staff can flag a chargeback call before the automated
        # dispute integration exists. Nothing else happens here.
        updated = store.update("payments", payment_id, {"status": new_status})

    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


@bp.post("/<payment_id>/refund")
def refund_payment(payment_id):
    """Refund a payment, in full or in part.

    Only a `captured` or `settled` payment has money to return. The requested
    amount is checked against the *remaining* refundable balance, not the
    original `amount` — otherwise two partial refunds could together exceed
    what was ever captured.
    """
    payment = _get_owned(payment_id)
    if payment["status"] not in ("captured", "settled"):
        raise ConflictError(
            "Only a captured or settled payment can be refunded.",
            code="invalid_state_transition",
        )

    body = request.get_json(silent=True) or {}
    refund_request = validate_refund_request(body, payment)

    refund = store.create(
        "refunds",
        {
            "payment_id": payment_id,
            "org_id": g.org_id,
            "amount": refund_request["amount"],
            "reason": refund_request["reason"],
            "status": "succeeded",
        },
    )
    updated = store.update(
        "payments",
        payment_id,
        {"amount_refunded": payment["amount_refunded"] + refund_request["amount"]},
    )

    from app.api.refunds import serialize_refund

    response = jsonify(serialize_refund(refund))
    response.status_code = 201
    response.headers["Location"] = "/v1/refunds/{}".format(refund["id"])
    return response


@bp.get("/<payment_id>/refunds")
def list_payment_refunds(payment_id):
    """Refunds scoped to one payment — a nested collection.

    Nesting stops at one level. `/payments/x/refunds/y/receipts` would be
    harder to use than a top-level `/refunds/y/receipts`.
    """
    from app.api.refunds import serialize_refund

    _get_owned(payment_id)
    records = [
        r
        for r in store.list("refunds")
        if r["payment_id"] == payment_id and r["org_id"] == g.org_id
    ]
    limit = parse_limit(request.args.get("limit"))
    page, next_cursor = paginate(
        records, limit=limit, cursor=request.args.get("cursor")
    )
    return jsonify(
        {
            "data": [serialize_refund(r) for r in page],
            "pagination": {
                "limit": limit,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
        }
    )


def _get_owned(payment_id):
    """Fetch and enforce tenant isolation.

    A payment belonging to another merchant returns 404, not 403 — 403 would
    confirm the ID exists, which leaks information across tenants.
    """
    payment = store.get("payments", payment_id)
    if payment["org_id"] != g.org_id:
        raise NotFoundError("Payment '{}' was not found.".format(payment_id))
    return payment
