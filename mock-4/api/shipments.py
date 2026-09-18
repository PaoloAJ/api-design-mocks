"""Shipment routes: CRUD, the transition action, and claims."""

from flask import Blueprint, g, jsonify, request

from core.errors import NotFoundError, ValidationError
from core.store import paginate, parse_limit, store
from services.shipping import STATES, apply_transition, assert_can_file_claim, validate_shipment

bp = Blueprint("shipments", __name__, url_prefix="/v1/shipments")

FIELDS = (
    "id destination carrier weight_kg state reference delivered_at "
    "created_at updated_at version"
).split()


def serialize(shipment):
    """Wire shape, decoupled from storage shape. `merchant_id` is internal, and
    pinning the field list means a new internal column cannot leak by accident."""
    return {field: shipment.get(field) for field in FIELDS}


def load_owned(shipment_id):
    """Keyed fetch, then tenant scope. Another merchant's shipment is a 404,
    never a 403: a 403 confirms the ID exists, enough to enumerate volume."""
    shipment = store.get("shipments", shipment_id)
    if shipment["merchant_id"] != g.merchant_id:
        raise NotFoundError("Shipment '{}' was not found.".format(shipment_id))
    return shipment


def with_etag(shipment, status=200):
    response = jsonify(serialize(shipment))
    response.status_code = status
    response.headers["ETag"] = '"{}"'.format(shipment["version"])
    return response


@bp.get("")
def list_shipments():
    """Filter, then paginate. Never the other way around."""
    records = [s for s in store.list("shipments") if s["merchant_id"] == g.merchant_id]

    state = request.args.get("state")
    if state:
        if state not in STATES:
            raise ValidationError(
                "Unknown state filter '{}'.".format(state),
                errors=[{"field": "state", "message": "must be one of " + ", ".join(sorted(STATES))}],
            )
        records = [s for s in records if s["state"] == state]

    carrier = request.args.get("carrier")
    if carrier:
        records = [s for s in records if s["carrier"] == carrier]

    page, next_cursor = paginate(
        records, limit=parse_limit(request.args.get("limit")), cursor=request.args.get("cursor")
    )
    return jsonify(
        {
            "data": [serialize(s) for s in page],
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
        }
    )


@bp.post("")
def create_shipment():
    """Create a label.

    Idempotency-Key lets a client retry a timed-out POST without booking two
    labels. Known gaps, deliberately: no TTL on the key, no fingerprint of the
    body, no in-flight handling, and the key is not scoped to the merchant.
    """
    key = request.headers.get("Idempotency-Key")
    if key:
        existing = store.lookup_idempotency(key)
        if existing:
            return jsonify(serialize(store.get(*existing))), 200

    shipment = store.create("shipments", validate_shipment(request.get_json(silent=True)))
    if key:
        store.remember_idempotency(key, "shipments", shipment["id"])

    response = with_etag(shipment, 201)
    response.headers["Location"] = "/v1/shipments/{}".format(shipment["id"])
    return response


@bp.get("/<shipment_id>")
def get_shipment(shipment_id):
    return with_etag(load_owned(shipment_id))


@bp.patch("/<shipment_id>")
def update_shipment(shipment_id):
    """Edit mutable fields under optimistic concurrency.

    If-Match carries the version the client last read. Without it two support
    agents editing the same shipment silently overwrite each other; with it the
    loser gets a 409 and re-reads.
    """
    load_owned(shipment_id)

    expected = None
    if_match = request.headers.get("If-Match")
    if if_match:
        try:
            expected = int(if_match.strip().lstrip("W/").strip('"'))
        except ValueError:
            raise ValidationError(
                "Malformed If-Match header.",
                errors=[{"field": "If-Match", "message": "expected a version ETag"}],
            )

    payload = request.get_json(silent=True) or {}
    if "state" in payload:
        raise ValidationError(
            "`state` is not writable here.",
            errors=[{"field": "state", "message": "use POST /v1/shipments/{id}/transition"}],
        )

    changes = {}
    if "destination" in payload:
        if not isinstance(payload["destination"], str) or not payload["destination"].strip():
            raise ValidationError(
                "The request body failed validation.",
                errors=[{"field": "destination", "message": "non-empty string"}],
            )
        changes["destination"] = payload["destination"].strip()
    if "reference" in payload:
        changes["reference"] = (payload["reference"] or "").strip() or None
    if not changes:
        raise ValidationError("No editable fields in request body.")

    return with_etag(store.update("shipments", shipment_id, changes, expected_version=expected))


@bp.post("/<shipment_id>/transition")
def transition_shipment(shipment_id):
    """Move a shipment through the lifecycle.

    An action sub-resource, not a PATCH, because a transition does more than
    set a field: it stamps derived timestamps and changes what the shipment is
    eligible for. Those side effects are the justification.
    """
    shipment = load_owned(shipment_id)
    to_state = (request.get_json(silent=True) or {}).get("to_state")
    if not to_state:
        raise ValidationError(
            "`to_state` is required.", errors=[{"field": "to_state", "message": "required"}]
        )
    changes = apply_transition(shipment, to_state, source="merchant")
    return jsonify(serialize(store.update("shipments", shipment_id, changes)))


@bp.post("/<shipment_id>/claims")
def file_claim(shipment_id):
    """File a loss/damage claim — the human action at the end of the flow."""
    shipment = load_owned(shipment_id)
    assert_can_file_claim(shipment)

    reason = (request.get_json(silent=True) or {}).get("reason")
    if reason not in {"lost", "damaged", "late"}:
        raise ValidationError(
            "The request body failed validation.",
            errors=[{"field": "reason", "message": "must be one of damaged, late, lost"}],
        )

    claim = store.create(
        "claims",
        {
            "merchant_id": g.merchant_id,
            "shipment_id": shipment_id,
            "reason": reason,
            "status": "open",
            "filed_by": g.merchant_id,
        },
    )
    body = {k: claim[k] for k in ("id", "shipment_id", "reason", "status", "created_at")}
    return jsonify(body), 201
