"""/v1/settlements — bulk settlement-outcome ingest.

A deliberately different shape from the CRUD resources. The settlement
processor reports outcomes for a batch of captured payments once its own
end-of-day cycle finishes, which changes the design:

  * Accepts an array so one request carries the whole batch (per-item HTTP
    overhead would dominate at real volume).
  * Returns 202 Accepted, not 200: outcomes are recorded, but this is the
    async boundary of the service — a payment sits `captured` for an
    unspecified stretch of real time before something calls this endpoint at
    all. `POST /v1/payments/{id}/status` cannot reach `settled` or `failed`
    for exactly this reason: only the processor gets to say a settlement
    finished.
  * Reports per-item results so one malformed row does not reject the batch.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.core.errors import ValidationError
from app.core.store import store, utcnow
from app.core.validation import require_json

bp = Blueprint("settlements", __name__, url_prefix="/v1/settlements")

MAX_ITEMS_PER_REQUEST = 500
OUTCOMES = {"settled", "failed"}

# Record of what was ingested, kept only so tests and the interviewer script
# can assert on it. Nothing in the API reads this back.
_ingested = []


@bp.post("")
def submit_settlements():
    body = require_json(request.get_json(silent=True))
    settlements = body.get("settlements")
    if not isinstance(settlements, list):
        raise ValidationError(
            "`settlements` must be an array.",
            errors=[{"field": "settlements", "message": "required array"}],
        )
    if not settlements:
        raise ValidationError(
            "`settlements` must contain at least one item.",
            errors=[{"field": "settlements", "message": "must not be empty"}],
        )
    if len(settlements) > MAX_ITEMS_PER_REQUEST:
        raise ValidationError(
            "At most {} settlements per request.".format(MAX_ITEMS_PER_REQUEST),
            code="payload_too_large",
            status_code=413,
            errors=[{"field": "settlements", "message": "too many items"}],
        )

    accepted = 0
    rejected = []

    for index, item in enumerate(settlements):
        error = _apply_settlement(item)
        if error is not None:
            # Index is included so the processor can map the failure back to
            # the exact row it sent.
            rejected.append({"index": index, "reason": error})
            continue
        accepted += 1

    status = 202 if accepted else 400
    return (
        jsonify(
            {
                "accepted": accepted,
                "rejected": len(rejected),
                "errors": rejected,
            }
        ),
        status,
    )


def _apply_settlement(item):
    if not isinstance(item, dict):
        return "must be an object"

    payment_id = item.get("payment_id")
    if not isinstance(payment_id, str) or not payment_id.strip():
        return "`payment_id` is required"

    outcome = item.get("outcome")
    if outcome not in OUTCOMES:
        return "`outcome` must be one of: {}".format(", ".join(sorted(OUTCOMES)))

    reference = item.get("reference")
    if reference is not None and not isinstance(reference, str):
        return "`reference` must be a string"

    payment = store.find_one("payments", id=payment_id, org_id=g.org_id)
    if payment is None:
        return "payment not found"
    if payment["status"] != "captured":
        return "payment is not awaiting settlement (status is '{}')".format(
            payment["status"]
        )

    store.update(
        "payments",
        payment_id,
        {
            "status": outcome,
            "settled_at": utcnow(),
            "settlement_reference": reference,
        },
    )
    _ingested.append({"org_id": g.org_id, "payment_id": payment_id, "outcome": outcome})
    return None
