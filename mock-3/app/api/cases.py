"""/v1/cases — the main resource: a piece of content awaiting a decision.

Shows the pieces an API-design interview usually probes: correct verbs and
status codes, filtering plus cursor pagination on the collection, optimistic
concurrency with ETag/If-Match, idempotent creates, and a sub-resource for a
state transition that is not a plain field write.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.pagination import paginate, parse_limit
from app.core.store import store, utcnow
from app.core.validation import (
    CASE_STATES,
    CONTENT_KINDS,
    QUEUES,
    validate_case_create,
    validate_case_patch,
    validate_appeal,
    validate_decision,
)

bp = Blueprint("cases", __name__, url_prefix="/v1/cases")

# A case that has been decided is closed to further decisions. Reopening one
# is what the appeal flow is for.
TERMINAL_STATES = {"approved", "removed"}


def serialize(case):
    """Shape the wire representation.

    Kept separate from storage so internal fields can exist without leaking,
    and so a v2 representation can be added without touching the store.
    """
    return {
        "id": case["id"],
        "external_id": case["external_id"],
        "kind": case["kind"],
        "text": case["text"],
        "state": case["state"],
        "queue": case["queue"],
        "reason": case["reason"],
        "labels": case["labels"],
        "score": case["score"],
        "decided_by": case["decided_by"],
        "decided_at": case["decided_at"],
        "platform_id": case["platform_id"],
        "version": case["version"],
        "created_at": case["created_at"],
        "updated_at": case["updated_at"],
        # Hypermedia-lite: tells a client where related resources live without
        # making them build URLs by string concatenation.
        "links": {
            "self": "/v1/cases/{}".format(case["id"]),
            "appeals": "/v1/cases/{}/appeals".format(case["id"]),
        },
    }


def etag_for(case):
    """Weak ETag derived from the version counter.

    Weak (`W/`) because it marks semantic equivalence, not byte equality — two
    responses with the same version are interchangeable even if key order or
    whitespace differs.
    """
    return 'W/"{}-{}"'.format(case["id"], case["version"])


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
def list_cases():
    """GET /v1/cases — filter, then paginate.

    Filters are AND-ed. `label` may repeat (`?label=lang:en&label=nsfw:high`)
    and all must match; repeated params beat a comma-joined string because
    label values can legitimately contain commas.
    """
    records = [c for c in store.list("cases") if c["platform_id"] == g.platform_id]

    state = request.args.get("state")
    if state:
        if state not in CASE_STATES:
            raise ValidationError(
                "Invalid state filter.",
                errors=[
                    {
                        "field": "state",
                        "message": "must be one of: {}".format(
                            ", ".join(sorted(CASE_STATES))
                        ),
                    }
                ],
            )
        records = [c for c in records if c["state"] == state]

    queue = request.args.get("queue")
    if queue:
        if queue not in QUEUES:
            raise ValidationError(
                "Invalid queue filter.",
                errors=[
                    {
                        "field": "queue",
                        "message": "must be one of: {}".format(", ".join(sorted(QUEUES))),
                    }
                ],
            )
        records = [c for c in records if c["queue"] == queue]

    kind = request.args.get("kind")
    if kind:
        if kind not in CONTENT_KINDS:
            raise ValidationError(
                "Invalid kind filter.",
                errors=[
                    {
                        "field": "kind",
                        "message": "must be one of: {}".format(
                            ", ".join(sorted(CONTENT_KINDS))
                        ),
                    }
                ],
            )
        records = [c for c in records if c["kind"] == kind]

    wanted = [l.strip().lower() for l in request.args.getlist("label") if l.strip()]
    if wanted:
        records = [c for c in records if all(l in c["labels"] for l in wanted)]

    # Substring match stands in for full-text search. At scale this belongs in
    # a search index, not a table scan.
    query = request.args.get("q")
    if query:
        needle = query.lower()
        records = [c for c in records if needle in c["text"].lower()]

    limit = parse_limit(request.args.get("limit"))
    page, next_cursor = paginate(records, limit=limit, cursor=request.args.get("cursor"))

    return jsonify(
        {
            "data": [serialize(c) for c in page],
            "pagination": {
                "limit": limit,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
        }
    )


@bp.post("")
def create_case():
    """POST /v1/cases — 201 with Location, idempotent on Idempotency-Key.

    POST is not naturally idempotent, so a client that times out cannot safely
    retry. The `Idempotency-Key` header fixes that: a repeat of the same key
    returns the original resource instead of opening a second case.
    """
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing = store.lookup_idempotency(idempotency_key)
        if existing:
            kind, resource_id = existing
            case = store.get(kind, resource_id)
            response = jsonify(serialize(case))
            response.status_code = 200  # 200, not 201 — nothing was created.
            response.headers["Location"] = "/v1/cases/{}".format(case["id"])
            response.headers["ETag"] = etag_for(case)
            response.headers["Idempotent-Replay"] = "true"
            return response

    payload = validate_case_create(request.get_json(silent=True))
    payload["platform_id"] = g.platform_id

    # One open case per piece of content, so a report storm on a viral post
    # does not put the same content in front of fifty reviewers.
    duplicate = store.find_one(
        "cases", platform_id=g.platform_id, external_id=payload["external_id"]
    )
    if duplicate is not None:
        raise ConflictError(
            "A case for content '{}' already exists.".format(payload["external_id"]),
            code="duplicate_case",
        )

    case = store.create("cases", payload)
    if idempotency_key:
        store.remember_idempotency(idempotency_key, "cases", case["id"])

    response = jsonify(serialize(case))
    response.status_code = 201
    response.headers["Location"] = "/v1/cases/{}".format(case["id"])
    response.headers["ETag"] = etag_for(case)
    return response


@bp.get("/<case_id>")
def get_case(case_id):
    """GET one — supports conditional requests via If-None-Match (304)."""
    case = _get_owned(case_id)
    etag = etag_for(case)
    if request.headers.get("If-None-Match") == etag:
        # Saves the client re-downloading a body it already has.
        return "", 304, {"ETag": etag}
    response = jsonify(serialize(case))
    response.headers["ETag"] = etag
    return response


@bp.patch("/<case_id>")
def patch_case(case_id):
    """PATCH — partial update of routing metadata, optionally If-Match guarded.

    Note what is *not* patchable: `state`. Moving a case to a decision has
    side effects, so it lives at POST /v1/cases/{id}/decision instead.
    """
    case = _get_owned(case_id)
    expected_version = _parse_if_match(request.headers.get("If-Match"))
    changes = validate_case_patch(request.get_json(silent=True), case)

    updated = store.update("cases", case_id, changes, expected_version=expected_version)
    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


@bp.delete("/<case_id>")
def delete_case(case_id):
    """DELETE — 204 with no body.

    Idempotent by definition, but we still 404 a second delete so a client can
    tell "I deleted it" from "it was never there". Both positions are
    defensible; see INTERVIEWER.md.
    """
    _get_owned(case_id)
    store.delete("cases", case_id)
    return "", 204


@bp.post("/<case_id>/decision")
def decide_case(case_id):
    """POST a decision as a sub-resource.

    A decision is an *event* with side effects — it stamps the reviewer and
    timestamp, and closes the case to further decisions — not a field
    assignment. Modeling it as its own endpoint keeps PATCH free of hidden
    behavior and gives the action a place to enforce who may take it.
    """
    case = _get_owned(case_id)

    # Only a reviewer decides. An ingest credential may submit content all day
    # and must never be able to clear it.
    if g.role != "reviewer":
        raise ForbiddenError(
            "This credential may not decide cases.", code="insufficient_role"
        )

    payload = validate_decision(request.get_json(silent=True))
    previous = case["state"]

    if previous in TERMINAL_STATES:
        # 409, not 400: the body is fine, it conflicts with current state.
        raise ConflictError(
            "Case '{}' was already decided ({}).".format(case_id, previous),
            code="already_decided",
        )

    decision = payload["decision"]
    changes = {
        "state": decision,
        "reason": payload["reason"],
        "decided_by": payload["reviewer"],
        "decided_at": utcnow(),
    }

    if decision == "removed":
        changes["queue"] = "standard"
    elif decision == "approved":
        # An approved case leaves the queue with no reason attached.
        changes["reason"] = "none"

    updated = store.update("cases", case_id, changes)

    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


@bp.get("/<case_id>/appeals")
def list_case_appeals(case_id):
    """Appeals scoped to one case — a nested collection.

    Nesting stops at one level. `/cases/x/appeals/y/comments` would be harder
    to use than a top-level `/appeals/y/comments`.
    """
    from app.api.appeals import serialize_appeal

    _get_owned(case_id)
    records = [
        a
        for a in store.list("appeals")
        if a["case_id"] == case_id and a["platform_id"] == g.platform_id
    ]
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


@bp.post("/<case_id>/appeals")
def open_appeal(case_id):
    """File an appeal against a decided case.

    409 when the case has not been decided: there is nothing to appeal yet.
    One open appeal per case, so a user retrying the form does not create a
    queue of identical appeals.
    """
    from app.api.appeals import serialize_appeal

    case = _get_owned(case_id)
    if case["state"] not in TERMINAL_STATES:
        raise ConflictError(
            "Case '{}' has not been decided yet.".format(case_id),
            code="not_decided",
        )

    payload = validate_appeal(request.get_json(silent=True))

    existing = store.find_one("appeals", case_id=case_id, state="open")
    if existing is not None:
        raise ConflictError(
            "Case '{}' already has an open appeal.".format(case_id),
            code="duplicate_appeal",
        )

    payload["case_id"] = case_id
    payload["platform_id"] = g.platform_id
    payload["state"] = "open"
    payload["resolved_by"] = None
    payload["resolved_at"] = None

    appeal = store.create("appeals", payload)
    response = jsonify(serialize_appeal(appeal))
    response.status_code = 201
    response.headers["Location"] = "/v1/appeals/{}".format(appeal["id"])
    return response


def _get_owned(case_id):
    """Fetch and enforce tenant isolation.

    A case belonging to another platform returns 404, not 403 — 403 would
    confirm the ID exists, which leaks information across tenants.
    """
    case = store.get("cases", case_id)
    if case["platform_id"] != g.platform_id:
        raise NotFoundError("Case '{}' was not found.".format(case_id))
    return case
