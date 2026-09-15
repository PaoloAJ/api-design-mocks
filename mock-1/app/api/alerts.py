"""/v1/alerts — a read-mostly resource derived from monitor transitions.

Alerts are not created directly by clients; they are a consequence of a
monitor changing state. The only write is acknowledgement, which is modeled as
a sub-resource action for the same reason status transitions are.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.pagination import paginate, parse_limit
from app.core.store import store, utcnow

bp = Blueprint("alerts", __name__, url_prefix="/v1/alerts")

ALERT_STATES = {"open", "acknowledged", "resolved"}
ALERT_SEVERITIES = {"warning", "critical"}


def serialize_alert(alert):
    return {
        "id": alert["id"],
        "monitor_id": alert["monitor_id"],
        "severity": alert["severity"],
        "state": alert["state"],
        "message": alert["message"],
        "acknowledged_by": alert.get("acknowledged_by"),
        "resolved_at": alert.get("resolved_at"),
        "created_at": alert["created_at"],
        "updated_at": alert["updated_at"],
        "links": {
            "self": "/v1/alerts/{}".format(alert["id"]),
            "monitor": "/v1/monitors/{}".format(alert["monitor_id"]),
        },
    }


@bp.get("")
def list_alerts():
    records = [a for a in store.list("alerts") if a["org_id"] == g.org_id]

    state = request.args.get("state")
    if state:
        if state not in ALERT_STATES:
            raise ValidationError(
                "Invalid state filter.",
                errors=[
                    {
                        "field": "state",
                        "message": "must be one of: {}".format(
                            ", ".join(sorted(ALERT_STATES))
                        ),
                    }
                ],
            )
        records = [a for a in records if a["state"] == state]

    severity = request.args.get("severity")
    if severity:
        if severity not in ALERT_SEVERITIES:
            raise ValidationError(
                "Invalid severity filter.",
                errors=[
                    {
                        "field": "severity",
                        "message": "must be one of: {}".format(
                            ", ".join(sorted(ALERT_SEVERITIES))
                        ),
                    }
                ],
            )
        records = [a for a in records if a["severity"] == severity]

    monitor_id = request.args.get("monitor_id")
    if monitor_id:
        records = [a for a in records if a["monitor_id"] == monitor_id]

    limit = parse_limit(request.args.get("limit"))
    page, next_cursor = paginate(
        records, limit=limit, cursor=request.args.get("cursor")
    )
    return jsonify(
        {
            "data": [serialize_alert(a) for a in page],
            "pagination": {
                "limit": limit,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
        }
    )


@bp.get("/<alert_id>")
def get_alert(alert_id):
    return jsonify(serialize_alert(_get_owned(alert_id)))


@bp.post("/<alert_id>/acknowledge")
def acknowledge_alert(alert_id):
    """Acknowledge an open alert.

    Returns 409 for an already-resolved alert: the request is well-formed, but
    conflicts with the resource's current state. That is exactly what 409 is
    for — 400 would wrongly suggest the client should edit the body.
    """
    alert = _get_owned(alert_id)
    body = request.get_json(silent=True) or {}
    user = body.get("user")
    if not isinstance(user, str) or not user.strip():
        raise ValidationError(
            "`user` is required.",
            errors=[{"field": "user", "message": "required, non-empty string"}],
        )

    if alert["state"] == "resolved":
        raise ConflictError(
            "Cannot acknowledge an alert that is already resolved.",
            code="invalid_state_transition",
        )
    if alert["state"] == "acknowledged":
        # Already in the target state — treat as a successful no-op so retries
        # are safe.
        return jsonify(serialize_alert(alert))

    updated = store.update(
        "alerts",
        alert_id,
        {"state": "acknowledged", "acknowledged_by": user.strip()},
    )
    return jsonify(serialize_alert(updated))


def _get_owned(alert_id):
    alert = store.get("alerts", alert_id)
    if alert["org_id"] != g.org_id:
        raise NotFoundError("Alert '{}' was not found.".format(alert_id))
    return alert
