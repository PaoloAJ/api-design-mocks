"""Carrier scan ingest — the asynchronous boundary.

Carriers push scans in batches on their own schedule. This endpoint is not
CRUD: it accepts the whole batch, returns 202 with a per-item result, and does
not promise that anything downstream has happened yet. Partial success is
normal — one unknown tracking number must not reject the other forty-nine.

What is deliberately NOT here: nothing notifies the merchant when a scan moves
a shipment into `exception`, and nothing reconciles a shipment that stops
receiving scans. Both live outside this service.
"""

from flask import Blueprint, g, jsonify, request

from core.errors import APIError, ValidationError
from core.store import store, utcnow
from services.shipping import apply_transition

bp = Blueprint("scans", __name__, url_prefix="/v1/scans")

MAX_BATCH = 50

# What a carrier scan code means for the shipment's state.
SCAN_STATES = {
    "accepted": "in_transit",
    "departed": "in_transit",
    "out_for_delivery": "out_for_delivery",
    "delivered": "delivered",
    "exception": "exception",
    # A carrier may report a failed delivery attempt. It is a valid scan code
    # and it is recorded, but it maps to no state of its own.
    "attempted": None,
}


@bp.post("")
def ingest_scans():
    """Accept a batch of scans and report per-item outcomes.

    202, not 201: we have durably accepted the batch, but a caller must not
    read this as "every shipment now reflects these scans". Each error carries
    its `index` so a carrier can retry exactly the failed rows.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("scans"), list):
        raise ValidationError(
            "Body must be an object with a `scans` array.",
            errors=[{"field": "scans", "message": "required, array"}],
        )

    scans = payload["scans"]
    if not scans:
        raise ValidationError(
            "`scans` must not be empty.", errors=[{"field": "scans", "message": "at least 1 item"}]
        )
    if len(scans) > MAX_BATCH:
        raise ValidationError(
            "Batch of {} exceeds the limit of {}.".format(len(scans), MAX_BATCH),
            errors=[{"field": "scans", "message": "max {} items".format(MAX_BATCH)}],
        )

    accepted, errors = [], []
    for index, scan in enumerate(scans):
        try:
            accepted.append(_apply_one(scan))
        except APIError as exc:
            # One bad row fails alone. Rejecting the batch would make a carrier
            # replay 49 good scans to retry one.
            errors.append({"index": index, "code": exc.code, "message": exc.message})

    response = jsonify({"accepted": accepted, "errors": errors, "received": len(scans)})
    response.status_code = 202
    return response


def _apply_one(scan):
    if not isinstance(scan, dict):
        raise ValidationError("Each scan must be an object.")

    shipment_id = scan.get("shipment_id")
    code = scan.get("code")
    if not shipment_id:
        raise ValidationError("`shipment_id` is required.")
    if code not in SCAN_STATES:
        raise ValidationError("Unknown scan code '{}'.".format(code))

    # Keyed lookup, then tenant scope. A carrier key is bound to one merchant,
    # so a scan for someone else's shipment is a 404 like any other.
    shipment = store.get("shipments", shipment_id)
    if shipment["merchant_id"] != g.merchant_id:
        raise ValidationError("Shipment '{}' was not found.".format(shipment_id), code="not_found")

    record = store.create(
        "scans",
        {
            "merchant_id": g.merchant_id,
            "shipment_id": shipment_id,
            "code": code,
            "location": (scan.get("location") or "").strip() or None,
        },
    )

    target = SCAN_STATES[code]
    if target and target != shipment["state"]:
        if target == "delivered":
            # The carrier is the source of truth for delivery. If they scanned
            # it delivered, it is delivered, whatever we think the state is.
            shipment = store.update(
                "shipments", shipment_id, {"state": "delivered", "delivered_at": utcnow()}
            )
        else:
            changes = apply_transition(shipment, target, source="scan")
            shipment = store.update("shipments", shipment_id, changes)

    return {"scan_id": record["id"], "shipment_id": shipment_id, "state": shipment["state"]}
