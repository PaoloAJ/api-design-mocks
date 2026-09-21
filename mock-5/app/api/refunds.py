"""/v1/refunds — a read-only resource created as a side effect.

Refunds are never created directly by clients through this blueprint; the
only write path is `POST /v1/payments/{id}/refund`, which is why there is no
`bp.post("")` here. Modeling the write on the payment keeps the refund amount
validated against that payment's remaining balance in one place.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.core.errors import NotFoundError
from app.core.pagination import paginate, parse_limit
from app.core.store import store

bp = Blueprint("refunds", __name__, url_prefix="/v1/refunds")


def serialize_refund(refund):
    return {
        "id": refund["id"],
        "payment_id": refund["payment_id"],
        "amount": refund["amount"],
        "reason": refund["reason"],
        "status": refund["status"],
        "created_at": refund["created_at"],
        "updated_at": refund["updated_at"],
        "links": {
            "self": "/v1/refunds/{}".format(refund["id"]),
            "payment": "/v1/payments/{}".format(refund["payment_id"]),
        },
    }


@bp.get("")
def list_refunds():
    records = [r for r in store.list("refunds") if r["org_id"] == g.org_id]

    payment_id = request.args.get("payment_id")
    if payment_id:
        records = [r for r in records if r["payment_id"] == payment_id]

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


@bp.get("/<refund_id>")
def get_refund(refund_id):
    return jsonify(serialize_refund(_get_owned(refund_id)))


def _get_owned(refund_id):
    refund = store.get("refunds", refund_id)
    if refund["org_id"] != g.org_id:
        raise NotFoundError("Refund '{}' was not found.".format(refund_id))
    return refund
