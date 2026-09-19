"""/v1/appeals — a read-mostly resource derived from decided cases.

An appeal is what a user files when their content was removed. Clients do not
create appeals here directly on a whim: an appeal is only meaningful against a
case that was actually decided, so it is opened through the case
(`POST /v1/cases/{id}/appeals`) and resolved through an action sub-resource,
for the same reason a decision is.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.pagination import paginate, parse_limit
from app.core.store import store, utcnow

bp = Blueprint("appeals", __name__, url_prefix="/v1/appeals")

APPEAL_STATES = {"open", "upheld", "overturned"}


def serialize_appeal(appeal):
    return {
        "id": appeal["id"],
        "case_id": appeal["case_id"],
        "state": appeal["state"],
        "submitted_by": appeal["submitted_by"],
        "statement": appeal["statement"],
        "resolved_by": appeal.get("resolved_by"),
        "resolved_at": appeal.get("resolved_at"),
        "created_at": appeal["created_at"],
        "updated_at": appeal["updated_at"],
        "links": {
            "self": "/v1/appeals/{}".format(appeal["id"]),
            "case": "/v1/cases/{}".format(appeal["case_id"]),
        },
    }


@bp.get("")
def list_appeals():
    records = [a for a in store.list("appeals") if a["platform_id"] == g.platform_id]

    state = request.args.get("state")
    if state:
        if state not in APPEAL_STATES:
            raise ValidationError(
                "Invalid state filter.",
                errors=[
                    {
                        "field": "state",
                        "message": "must be one of: {}".format(
                            ", ".join(sorted(APPEAL_STATES))
                        ),
                    }
                ],
            )
        records = [a for a in records if a["state"] == state]

    case_id = request.args.get("case_id")
    if case_id:
        records = [a for a in records if a["case_id"] == case_id]

    limit = parse_limit(request.args.get("limit"))
    page, next_cursor = paginate(records, limit=limit, cursor=request.args.get("cursor"))
    return jsonify(
        {
            "data": [serialize_appeal(a) for a in page],
            "pagination": {
                "limit": limit,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
        }
    )


@bp.get("/<appeal_id>")
def get_appeal(appeal_id):
    return jsonify(serialize_appeal(_get_owned(appeal_id)))


@bp.post("/<appeal_id>/resolve")
def resolve_appeal(appeal_id):
    """Resolve an open appeal, either upholding or overturning the decision.

    Overturning is the one path that moves a case out of a terminal state, and
    it is why `decision` refuses to touch an already-decided case: there is
    exactly one way back, and it is audited.
    """
    appeal = _get_owned(appeal_id)
    body = request.get_json(silent=True) or {}

    outcome = body.get("outcome")
    if outcome not in ("upheld", "overturned"):
        raise ValidationError(
            "`outcome` must be `upheld` or `overturned`.",
            errors=[{"field": "outcome", "message": "must be upheld or overturned"}],
        )
    reviewer = body.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValidationError(
            "`reviewer` is required.",
            errors=[{"field": "reviewer", "message": "required, non-empty string"}],
        )

    if appeal["state"] != "open":
        # Already resolved. 409 because the body is well-formed but conflicts
        # with the resource's current state — 400 would wrongly suggest the
        # client should edit what it sent.
        raise ConflictError(
            "Appeal '{}' is already {}.".format(appeal_id, appeal["state"]),
            code="invalid_state_transition",
        )

    updated = store.update(
        "appeals",
        appeal_id,
        {
            "state": outcome,
            "resolved_by": reviewer.strip(),
            "resolved_at": utcnow(),
        },
    )

    if outcome == "overturned":
        # Overturning restores the content: the case returns to approved and
        # the removal reason is cleared.
        store.update(
            "cases",
            appeal["case_id"],
            {
                "state": "approved",
                "reason": "none",
                "decided_by": reviewer.strip(),
                "decided_at": utcnow(),
            },
        )

    return jsonify(serialize_appeal(updated))


def _get_owned(appeal_id):
    appeal = store.get("appeals", appeal_id)
    if appeal["platform_id"] != g.platform_id:
        raise NotFoundError("Appeal '{}' was not found.".format(appeal_id))
    return appeal
