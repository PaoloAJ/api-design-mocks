"""/v1/monitors — the main CRUD resource.

Shows the pieces an API-design interview usually probes: correct verbs and
status codes, filtering + cursor pagination on the collection, optimistic
concurrency with ETag/If-Match, idempotent creates, and a sub-resource for a
state transition that is not a plain field write.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request, url_for

from app.core.errors import ConflictError, ValidationError
from app.core.pagination import paginate, parse_limit
from app.core.store import store, utcnow
from app.core.validation import (
    MONITOR_STATUSES,
    MONITOR_TYPES,
    validate_monitor_create,
    validate_monitor_patch,
    validate_status_transition,
)

bp = Blueprint("monitors", __name__, url_prefix="/v1/monitors")


def serialize(monitor):
    """Shape the wire representation.

    Kept separate from storage so internal fields can exist without leaking,
    and so a v2 representation can be added without touching the store.
    """
    return {
        "id": monitor["id"],
        "name": monitor["name"],
        "type": monitor["type"],
        "query": monitor["query"],
        "status": monitor["status"],
        "thresholds": monitor["thresholds"],
        "tags": monitor["tags"],
        "enabled": monitor["enabled"],
        "org_id": monitor["org_id"],
        "version": monitor["version"],
        "created_at": monitor["created_at"],
        "updated_at": monitor["updated_at"],
        # Hypermedia-lite: tells a client where the related alerts live
        # without making them build URLs by string concatenation.
        "links": {
            "self": "/v1/monitors/{}".format(monitor["id"]),
            "alerts": "/v1/monitors/{}/alerts".format(monitor["id"]),
        },
    }


def etag_for(monitor):
    """Weak ETag derived from the version counter.

    Weak (`W/`) because it marks semantic equivalence, not byte equality —
    two responses with the same version are interchangeable even if key order
    or whitespace differs.
    """
    return 'W/"{}-{}"'.format(monitor["id"], monitor["version"])


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
def list_monitors():
    """GET /v1/monitors — filter, sort, paginate.

    Filters are AND-ed. `tag` may repeat (`?tag=env:prod&tag=team:api`) and
    all must match; repeated params beat a comma-joined string because tag
    values can legitimately contain commas.
    """
    records = [m for m in store.list("monitors") if m["org_id"] == g.org_id]

    status = request.args.get("status")
    if status:
        if status not in MONITOR_STATUSES:
            raise ValidationError(
                "Invalid status filter.",
                errors=[
                    {
                        "field": "status",
                        "message": "must be one of: {}".format(
                            ", ".join(sorted(MONITOR_STATUSES))
                        ),
                    }
                ],
            )
        records = [m for m in records if m["status"] == status]

    monitor_type = request.args.get("type")
    if monitor_type:
        if monitor_type not in MONITOR_TYPES:
            raise ValidationError(
                "Invalid type filter.",
                errors=[
                    {
                        "field": "type",
                        "message": "must be one of: {}".format(
                            ", ".join(sorted(MONITOR_TYPES))
                        ),
                    }
                ],
            )
        records = [m for m in records if m["type"] == monitor_type]

    wanted_tags = [t.strip().lower() for t in request.args.getlist("tag") if t.strip()]
    if wanted_tags:
        records = [m for m in records if all(t in m["tags"] for t in wanted_tags)]

    enabled = request.args.get("enabled")
    if enabled is not None:
        if enabled.lower() not in ("true", "false"):
            raise ValidationError(
                "`enabled` filter must be `true` or `false`.",
                errors=[{"field": "enabled", "message": "must be true or false"}],
            )
        records = [m for m in records if m["enabled"] == (enabled.lower() == "true")]

    # Substring match stands in for full-text search. At scale this belongs in
    # a search index, not a table scan.
    query = request.args.get("q")
    if query:
        needle = query.lower()
        records = [
            m
            for m in records
            if needle in m["name"].lower() or needle in m["query"].lower()
        ]

    limit = parse_limit(request.args.get("limit"))
    page, next_cursor = paginate(
        records, limit=limit, cursor=request.args.get("cursor")
    )

    return jsonify(
        {
            "data": [serialize(m) for m in page],
            "pagination": {
                "limit": limit,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
        }
    )


@bp.post("")
def create_monitor():
    """POST /v1/monitors — 201 with Location, idempotent on Idempotency-Key.

    POST is not naturally idempotent, so a client that times out cannot safely
    retry. The `Idempotency-Key` header fixes that: a repeat of the same key
    returns the original resource instead of creating a second monitor.
    """
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing = store.lookup_idempotency(idempotency_key)
        if existing:
            kind, resource_id = existing
            monitor = store.get(kind, resource_id)
            response = jsonify(serialize(monitor))
            response.status_code = 200  # 200, not 201 — nothing was created.
            response.headers["Location"] = "/v1/monitors/{}".format(monitor["id"])
            response.headers["ETag"] = etag_for(monitor)
            response.headers["Idempotent-Replay"] = "true"
            return response

    payload = validate_monitor_create(request.get_json(silent=True))
    payload["org_id"] = g.org_id

    # Names are unique per org so a retry storm cannot silently create twenty
    # identical monitors.
    duplicate = store.find_one("monitors", org_id=g.org_id, name=payload["name"])
    if duplicate is not None:
        raise ConflictError(
            "A monitor named '{}' already exists.".format(payload["name"]),
            code="duplicate_monitor",
        )

    monitor = store.create("monitors", payload)
    if idempotency_key:
        store.remember_idempotency(idempotency_key, "monitors", monitor["id"])

    response = jsonify(serialize(monitor))
    response.status_code = 201
    response.headers["Location"] = "/v1/monitors/{}".format(monitor["id"])
    response.headers["ETag"] = etag_for(monitor)
    return response


@bp.get("/<monitor_id>")
def get_monitor(monitor_id):
    """GET one — supports conditional requests via If-None-Match (304)."""
    monitor = _get_owned(monitor_id)
    etag = etag_for(monitor)
    if request.headers.get("If-None-Match") == etag:
        # Saves the client re-downloading a body it already has.
        return "", 304, {"ETag": etag}
    response = jsonify(serialize(monitor))
    response.headers["ETag"] = etag
    return response


@bp.patch("/<monitor_id>")
def patch_monitor(monitor_id):
    """PATCH — partial update, optionally guarded by If-Match.

    Without If-Match this is last-write-wins. With it, a stale client gets 409
    instead of silently clobbering someone else's edit.
    """
    monitor = _get_owned(monitor_id)
    expected_version = _parse_if_match(request.headers.get("If-Match"))
    changes = validate_monitor_patch(request.get_json(silent=True), monitor)

    if "name" in changes and changes["name"] != monitor["name"]:
        duplicate = store.find_one("monitors", org_id=g.org_id, name=changes["name"])
        if duplicate is not None:
            raise ConflictError(
                "A monitor named '{}' already exists.".format(changes["name"]),
                code="duplicate_monitor",
            )

    updated = store.update(
        "monitors", monitor_id, changes, expected_version=expected_version
    )
    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


@bp.delete("/<monitor_id>")
def delete_monitor(monitor_id):
    """DELETE — 204 with no body.

    Idempotent by definition, but we still 404 a second delete so a client can
    tell "I deleted it" from "it was never there". Both positions are
    defensible; see INTERVIEWER.md.
    """
    _get_owned(monitor_id)
    store.delete("monitors", monitor_id)
    return "", 204


@bp.post("/<monitor_id>/status")
def transition_status(monitor_id):
    """POST a state transition as a sub-resource.

    A status change is an *event* with side effects (it opens or resolves an
    alert), not a field assignment. Modeling it as its own endpoint keeps
    PATCH free of hidden behavior and gives the action a place to live.
    """
    monitor = _get_owned(monitor_id)
    new_status = validate_status_transition(request.get_json(silent=True))
    previous = monitor["status"]

    if previous == new_status:
        # No-op transitions must not spawn duplicate alerts.
        response = jsonify(serialize(monitor))
        response.headers["ETag"] = etag_for(monitor)
        return response

    updated = store.update("monitors", monitor_id, {"status": new_status})

    if new_status in ("warn", "alert"):
        store.create(
            "alerts",
            {
                "monitor_id": monitor_id,
                "org_id": g.org_id,
                "severity": "critical" if new_status == "alert" else "warning",
                "state": "open",
                "message": "Monitor '{}' transitioned {} -> {}".format(
                    monitor["name"], previous, new_status
                ),
                "resolved_at": None,
            },
        )
    elif new_status == "ok":
        for alert in store.list("alerts"):
            if alert["monitor_id"] == monitor_id and alert["state"] == "open":
                store.update(
                    "alerts",
                    alert["id"],
                    {"state": "resolved", "resolved_at": utcnow()},
                )

    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


@bp.get("/<monitor_id>/alerts")
def list_monitor_alerts(monitor_id):
    """Alerts scoped to one monitor — a nested collection.

    Nesting stops at one level. `/monitors/x/alerts/y/comments` would be
    harder to use than a top-level `/alerts/y/comments`.
    """
    from app.api.alerts import serialize_alert

    _get_owned(monitor_id)
    records = [
        a
        for a in store.list("alerts")
        if a["monitor_id"] == monitor_id and a["org_id"] == g.org_id
    ]
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


def _get_owned(monitor_id):
    """Fetch and enforce tenant isolation.

    A monitor belonging to another org returns 404, not 403 — 403 would
    confirm the ID exists, which leaks information across tenants.
    """
    monitor = store.get("monitors", monitor_id)
    if monitor["org_id"] != g.org_id:
        from app.core.errors import NotFoundError

        raise NotFoundError("Monitor '{}' was not found.".format(monitor_id))
    return monitor
