"""/v1/incidents — the main resource: lifecycle, responders, timeline.

Contrasts deliberately with mock-1's monitors, on purpose — the point of
running both mocks is to see the same primitives (ETags, idempotency, cursor
pagination, tenant isolation) applied to two different judgment calls:

  * The status machine is a strict allow-list (`ALLOWED_TRANSITIONS`), not
    monitors' any-state-to-any-state.
  * There is no DELETE — an incident is an audit record, not disposable
    state. (Try it: `DELETE /v1/incidents/{id}` is a 405, not a 204.)
  * Adding a responder is naturally idempotent (posting the same handle twice
    is a no-op) — contrast with `POST /v1/incidents` itself, which needs an
    explicit `Idempotency-Key` header to be retry-safe.
  * Severity has no write path at all. See `pr_review/` for a PR that adds
    one, planted with bugs to review — that is this codebase's version of
    the interview's third phase.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.pagination import paginate, parse_limit
from app.core.store import store, utcnow
from app.core.validation import (
    SEVERITIES,
    STATUSES,
    validate_incident_create,
    validate_incident_patch,
    validate_responder,
    validate_status_transition,
    validate_timeline_note,
)

bp = Blueprint("incidents", __name__, url_prefix="/v1/incidents")

# Strict, directed transition graph — contrast with monitors' any-to-any.
# `resolved` is reachable from every open state; only `resolved` can move
# again, and only back to `triggered` (an explicit reopen).
ALLOWED_TRANSITIONS = {
    "triggered": {"acknowledged", "resolved"},
    "acknowledged": {"investigating", "resolved"},
    "investigating": {"monitoring", "resolved"},
    "monitoring": {"investigating", "resolved"},
    "resolved": {"triggered"},
}


def serialize(incident):
    return {
        "id": incident["id"],
        "title": incident["title"],
        "summary": incident["summary"],
        "severity": incident["severity"],
        "status": incident["status"],
        "service_id": incident["service_id"],
        "commander": incident["commander"],
        "responders": incident["responders"],
        "tags": incident["tags"],
        "org_id": incident["org_id"],
        "resolved_at": incident["resolved_at"],
        "version": incident["version"],
        "created_at": incident["created_at"],
        "updated_at": incident["updated_at"],
        "links": {
            "self": "/v1/incidents/{}".format(incident["id"]),
            "timeline": "/v1/incidents/{}/timeline".format(incident["id"]),
        },
    }


def serialize_timeline_entry(entry):
    return {
        "id": entry["id"],
        "incident_id": entry["incident_id"],
        "kind": entry["kind"],
        "author": entry["author"],
        "message": entry["message"],
        "created_at": entry["created_at"],
    }


def etag_for(incident):
    """Weak ETag derived from the version counter — see mock-1 for why weak."""
    return 'W/"{}-{}"'.format(incident["id"], incident["version"])


def _parse_if_match(header):
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


def _add_timeline_entry(incident_id, kind, author, message):
    store.create(
        "timeline_entries",
        {
            "incident_id": incident_id,
            "org_id": g.org_id,
            "kind": kind,
            "author": author,
            "message": message,
        },
    )


@bp.get("")
def list_incidents():
    """Filters are AND-ed, same convention as mock-1's monitor list."""
    records = [i for i in store.list("incidents") if i["org_id"] == g.org_id]

    status = request.args.get("status")
    if status:
        if status not in STATUSES:
            raise ValidationError(
                "Invalid status filter.",
                errors=[
                    {
                        "field": "status",
                        "message": "must be one of: {}".format(
                            ", ".join(sorted(STATUSES))
                        ),
                    }
                ],
            )
        records = [i for i in records if i["status"] == status]

    severity = request.args.get("severity")
    if severity:
        if severity not in SEVERITIES:
            raise ValidationError(
                "Invalid severity filter.",
                errors=[{"field": "severity", "message": "must be one of: sev1, sev2, sev3, sev4"}],
            )
        records = [i for i in records if i["severity"] == severity]

    service_id = request.args.get("service_id")
    if service_id:
        records = [i for i in records if i["service_id"] == service_id]

    wanted_tags = [t.strip().lower() for t in request.args.getlist("tag") if t.strip()]
    if wanted_tags:
        records = [i for i in records if all(t in i["tags"] for t in wanted_tags)]

    # Substring match stands in for full-text search. At scale this belongs in
    # a search index, not a table scan — same caveat as mock-1's `q`.
    query = request.args.get("q")
    if query:
        needle = query.lower()
        records = [
            i
            for i in records
            if needle in i["title"].lower() or needle in (i["summary"] or "").lower()
        ]

    limit = parse_limit(request.args.get("limit"))
    page, next_cursor = paginate(
        records, limit=limit, cursor=request.args.get("cursor")
    )
    return jsonify(
        {
            "data": [serialize(i) for i in page],
            "pagination": {
                "limit": limit,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
        }
    )


@bp.post("")
def create_incident():
    """POST /v1/incidents — 201 with Location, idempotent on Idempotency-Key.

    Creating a monitor and creating an incident share the same problem (POST
    is not naturally idempotent, so a timed-out client cannot safely retry)
    and the same fix. What differs is `commander`: supplying one on create
    seeds `responders` with them, which is why this handler writes two
    timeline entries instead of one.
    """
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing = store.lookup_idempotency(idempotency_key)
        if existing:
            kind, resource_id = existing
            incident = store.get(kind, resource_id)
            response = jsonify(serialize(incident))
            response.status_code = 200  # 200, not 201 — nothing was created.
            response.headers["Location"] = "/v1/incidents/{}".format(incident["id"])
            response.headers["ETag"] = etag_for(incident)
            response.headers["Idempotent-Replay"] = "true"
            return response

    payload = validate_incident_create(request.get_json(silent=True))

    service = store.find_one("services", id=payload["service_id"], org_id=g.org_id)
    if service is None:
        raise ValidationError(
            "No such service.",
            errors=[{"field": "service_id", "message": "does not exist in this org"}],
        )

    payload["org_id"] = g.org_id
    if payload["commander"]:
        payload["responders"] = [payload["commander"]]

    incident = store.create("incidents", payload)
    _add_timeline_entry(incident["id"], "created", "system", "Incident created")
    if incident["commander"]:
        _add_timeline_entry(
            incident["id"],
            "responder_added",
            "system",
            "{} added as commander".format(incident["commander"]),
        )

    if idempotency_key:
        store.remember_idempotency(idempotency_key, "incidents", incident["id"])

    response = jsonify(serialize(incident))
    response.status_code = 201
    response.headers["Location"] = "/v1/incidents/{}".format(incident["id"])
    response.headers["ETag"] = etag_for(incident)
    return response


@bp.get("/<incident_id>")
def get_incident(incident_id):
    """GET one — supports conditional requests via If-None-Match (304)."""
    incident = _get_owned(incident_id)
    etag = etag_for(incident)
    if request.headers.get("If-None-Match") == etag:
        return "", 304, {"ETag": etag}
    response = jsonify(serialize(incident))
    response.headers["ETag"] = etag
    return response


@bp.patch("/<incident_id>")
def patch_incident(incident_id):
    """PATCH — partial update, optionally guarded by If-Match.

    Without If-Match this is last-write-wins. With it, a stale client gets
    409 instead of silently clobbering someone else's edit. `severity` and
    `status` are not patchable — see validate_incident_patch's docstring.
    """
    incident = _get_owned(incident_id)
    expected_version = _parse_if_match(request.headers.get("If-Match"))
    changes = validate_incident_patch(request.get_json(silent=True))
    updated = store.update(
        "incidents", incident_id, changes, expected_version=expected_version
    )
    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


# Deliberately no @bp.delete("/<incident_id>") — an incident is an audit
# record, not disposable state, so a client hitting DELETE here gets Flask's
# own 405, funneled through the same JSON envelope as every other error.


@bp.post("/<incident_id>/status")
def transition_status(incident_id):
    """POST a state transition as a sub-resource, same rationale as mock-1:

    a status change is an *event* with side effects (it may resolve or
    reopen the incident and always writes a timeline entry), not a plain
    field assignment. Unlike mock-1's monitors, the transition is also
    checked against `ALLOWED_TRANSITIONS` and rejected with 409 if it is not
    a legal edge.
    """
    incident = _get_owned(incident_id)
    new_status = validate_status_transition(request.get_json(silent=True))
    previous = incident["status"]

    if previous == new_status:
        # No-op transitions must not spawn duplicate timeline entries.
        response = jsonify(serialize(incident))
        response.headers["ETag"] = etag_for(incident)
        return response

    if new_status not in ALLOWED_TRANSITIONS.get(previous, set()):
        raise ConflictError(
            "Cannot move an incident from '{}' to '{}'.".format(previous, new_status),
            code="invalid_state_transition",
        )

    changes = {"status": new_status}
    if new_status == "resolved":
        changes["resolved_at"] = utcnow()
        message = "Incident resolved (was {})".format(previous)
    elif previous == "resolved":
        changes["resolved_at"] = None
        message = "Incident reopened"
    else:
        message = "Status changed: {} -> {}".format(previous, new_status)

    updated = store.update("incidents", incident_id, changes)
    _add_timeline_entry(incident_id, "status_change", "system", message)

    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


@bp.post("/<incident_id>/responders")
def add_responder(incident_id):
    """Naturally idempotent: adding an already-present responder is a no-op.

    Contrast with `POST /v1/incidents`, whose idempotency needs an explicit
    `Idempotency-Key` header. The difference is that "add this responder" has
    no meaningful notion of a *duplicate* the way "create an incident" does —
    the target state fully describes the operation, so replaying it is safe
    for free, with no header and no stored key.
    """
    incident = _get_owned(incident_id)
    handle = validate_responder(request.get_json(silent=True))

    if handle in incident["responders"]:
        response = jsonify(serialize(incident))
        response.headers["ETag"] = etag_for(incident)
        return response

    updated = store.update(
        "incidents", incident_id, {"responders": incident["responders"] + [handle]}
    )
    _add_timeline_entry(
        incident_id, "responder_added", "system", "{} added".format(handle)
    )
    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


@bp.delete("/<incident_id>/responders/<handle>")
def remove_responder(incident_id, handle):
    """Unlike the incident itself, a responder *is* disposable state, so this
    one does get a real DELETE. Removing someone not on the incident is a
    404, matching mock-1's stance that a second DELETE should say so rather
    than silently succeed twice.
    """
    incident = _get_owned(incident_id)
    if handle not in incident["responders"]:
        raise NotFoundError("Responder '{}' is not on this incident.".format(handle))
    remaining = [h for h in incident["responders"] if h != handle]
    store.update("incidents", incident_id, {"responders": remaining})
    _add_timeline_entry(
        incident_id, "responder_removed", "system", "{} removed".format(handle)
    )
    return "", 204


@bp.get("/<incident_id>/timeline")
def list_timeline(incident_id):
    _get_owned(incident_id)
    records = [
        t
        for t in store.list("timeline_entries")
        if t["incident_id"] == incident_id and t["org_id"] == g.org_id
    ]
    limit = parse_limit(request.args.get("limit"))
    page, next_cursor = paginate(
        records, limit=limit, cursor=request.args.get("cursor")
    )
    return jsonify(
        {
            "data": [serialize_timeline_entry(t) for t in page],
            "pagination": {
                "limit": limit,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
        }
    )


@bp.post("/<incident_id>/timeline")
def add_timeline_note(incident_id):
    """Append a user note. There is no PATCH or DELETE for a single timeline
    entry, anywhere in this file — it is an append-only audit log, not an
    editable resource. Once written, an entry is part of the incident's
    history even if it turns out to be wrong; you append a correction, you
    don't rewrite what happened. Notes are accepted even on a resolved
    incident: that is exactly where postmortem notes get written.
    """
    _get_owned(incident_id)
    note = validate_timeline_note(request.get_json(silent=True))
    entry = store.create(
        "timeline_entries",
        {
            "incident_id": incident_id,
            "org_id": g.org_id,
            "kind": "note",
            "author": note["author"],
            "message": note["message"],
        },
    )
    response = jsonify(serialize_timeline_entry(entry))
    response.status_code = 201
    response.headers["Location"] = "/v1/incidents/{}/timeline/{}".format(
        incident_id, entry["id"]
    )
    return response


def _get_owned(incident_id):
    """Fetch and enforce tenant isolation.

    An incident belonging to another org returns 404, not 403 — 403 would
    confirm the ID exists, which leaks information across tenants. Every
    handler above that touches a specific incident goes through this
    function; `pr_review/` contains a new endpoint that doesn't.
    """
    incident = store.get("incidents", incident_id)
    if incident["org_id"] != g.org_id:
        raise NotFoundError("Incident '{}' was not found.".format(incident_id))
    return incident
