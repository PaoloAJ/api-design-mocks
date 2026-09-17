"""Shipment lifecycle: validation, the state machine, and claim eligibility.

Route handlers stay thin and HTTP-shaped; the rules that decide whether a
transition is legal live here, so the same rules apply whether a state change
arrives from a merchant call or from a carrier scan batch.

The lifecycle:

    label_created -> in_transit -> out_for_delivery -> delivered
                          |              |
                          +--------------+---> exception

`delivered` and `cancelled` are terminal. `exception` is not: a package that
misses a scan can recover to `in_transit` when the next scan lands.
"""

from flask import g

from core.errors import ConflictError, ForbiddenError, ValidationError
from core.store import store, utcnow

STATES = {
    "label_created",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "exception",
    "cancelled",
}

# Only these moves are legal. Anything absent is a 409, not a 400: the request
# is well-formed, it just conflicts with the shipment's current state.
TRANSITIONS = {
    "label_created": {"in_transit", "cancelled"},
    "in_transit": {"out_for_delivery", "exception", "cancelled"},
    "out_for_delivery": {"delivered", "exception"},
    "exception": {"in_transit", "delivered"},
    "delivered": set(),
    "cancelled": set(),
}

# A claim is only meaningful once the carrier has taken custody. Filing
# against a label nobody has scanned is a merchant-side mistake.
CLAIMABLE_STATES = {"in_transit", "out_for_delivery", "delivered", "exception"}

CARRIERS = {"ups", "fedex", "usps", "dhl"}
MAX_WEIGHT_KG = 68.0  # carrier parcel ceiling; above this is freight


def validate_shipment(payload):
    """Collect every field error, not just the first.

    A client fixing one field per round trip is a bad API. Returning all of
    them lets a form highlight every bad input in one pass.
    """
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")

    errors = []
    destination = payload.get("destination")
    if not isinstance(destination, str) or not destination.strip():
        errors.append({"field": "destination", "message": "required, non-empty string"})

    carrier = payload.get("carrier")
    if carrier not in CARRIERS:
        errors.append({"field": "carrier", "message": "must be one of " + ", ".join(sorted(CARRIERS))})

    weight = payload.get("weight_kg")
    if not isinstance(weight, (int, float)) or isinstance(weight, bool):
        errors.append({"field": "weight_kg", "message": "required, number"})
    elif weight <= 0:
        errors.append({"field": "weight_kg", "message": "must be > 0"})
    elif weight > MAX_WEIGHT_KG:
        errors.append({"field": "weight_kg", "message": "exceeds {}kg parcel limit".format(MAX_WEIGHT_KG)})

    if "state" in payload:
        # Rule 5: state moves only through the action sub-resource.
        errors.append({"field": "state", "message": "not writable on create; use POST /v1/shipments/{id}/transition"})

    if errors:
        raise ValidationError("The request body failed validation.", errors=errors)

    return {
        "merchant_id": g.merchant_id,
        "destination": destination.strip(),
        "carrier": carrier,
        "weight_kg": float(weight),
        "state": "label_created",
        "reference": (payload.get("reference") or "").strip() or None,
        "delivered_at": None,
    }


def apply_transition(shipment, to_state, *, source):
    """Move a shipment and return the fields that changed.

    `source` is "merchant" or "scan" — the scan path reuses this so a carrier
    batch cannot make a move a merchant could not.
    """
    if to_state not in STATES:
        raise ValidationError(
            "Unknown state '{}'.".format(to_state),
            errors=[{"field": "to_state", "message": "must be one of " + ", ".join(sorted(STATES))}],
        )

    current = shipment["state"]
    if to_state not in TRANSITIONS[current]:
        raise ConflictError(
            "Cannot move a shipment from '{}' to '{}'.".format(current, to_state),
            code="illegal_transition",
        )

    changes = {"state": to_state}

    # Side effects that justify this being an action sub-resource rather than
    # a PATCH on `state`: each transition stamps derived fields and, for
    # delivery, closes the window in which a claim may be filed.
    if to_state == "delivered":
        changes["delivered_at"] = utcnow()
    elif to_state == "cancelled":
        changes["cancelled_by"] = g.merchant_id
    elif to_state == "exception":
        changes["exception_source"] = source

    return changes


def assert_can_file_claim(shipment):
    if g.role != "merchant":
        # Known caller, wrong role — 403, not 404. The carrier key legitimately
        # belongs to this merchant; it just may not speak for them.
        raise ForbiddenError("Only a merchant key may file a claim.", code="role_required")
    if shipment["state"] not in CLAIMABLE_STATES:
        raise ConflictError(
            "A claim cannot be filed against a shipment in '{}'.".format(shipment["state"]),
            code="not_claimable",
        )
    if store.find_one("claims", shipment_id=shipment["id"], status="open"):
        raise ConflictError("An open claim already exists for this shipment.", code="duplicate_claim")
