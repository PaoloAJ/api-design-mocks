"""/v1/projects/<slug>/builds — the main resource.

A build is created queued, moves to running, and ends in a terminal state.
Jobs are a side effect of those transitions, never created directly.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.pagination import paginate, parse_limit
from app.core.store import store, utcnow
from app.core.validation import (
    BUILD_STATES,
    TERMINAL_STATES,
    TRIGGERS,
    validate_build_create,
    validate_transition,
)

bp = Blueprint("builds", __name__, url_prefix="/v1/projects/<slug>/builds")


def serialize(build):
    """Shape the wire representation.

    Kept separate from storage so internal fields can exist without leaking,
    and so a v2 shape can be added without touching the store.
    """
    return {
        "id": build["id"],
        "project_id": build["project_id"],
        "branch": build["branch"],
        "commit": build["commit"],
        "trigger": build["trigger"],
        "state": build["state"],
        "jobs": build["jobs"],
        "started_at": build["started_at"],
        "finished_at": build["finished_at"],
        "version": build["version"],
        "created_at": build["created_at"],
        "updated_at": build["updated_at"],
        "links": {
            "self": "/v1/projects/{}/builds/{}".format(
                build["project_slug"], build["id"]
            ),
            "jobs": "/v1/projects/{}/builds/{}/jobs".format(
                build["project_slug"], build["id"]
            ),
        },
    }


def etag_for(build):
    """Weak ETag from the version counter.

    Weak (`W/`) because it marks semantic equivalence, not byte equality: two
    responses at the same version are interchangeable even if key order differs.
    """
    return 'W/"{}-{}"'.format(build["id"], build["version"])


def _parse_if_match(header):
    """Pull the version integer out of an `If-Match` ETag.

    `*` means "any existing version" — the client only asserts the build
    exists, so no version check runs.
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
def list_builds(slug):
    """GET the collection — filter, then paginate.

    Filters are AND-ed and applied before pagination, so `has_more` and the
    page size stay truthful.
    """
    records = [b for b in store.list("builds") if b["project_id"] == g.project["id"]]

    state = request.args.get("state")
    if state:
        if state not in BUILD_STATES:
            raise ValidationError(
                "Invalid state filter.",
                errors=[
                    {
                        "field": "state",
                        "message": "must be one of: {}".format(
                            ", ".join(sorted(BUILD_STATES))
                        ),
                    }
                ],
            )
        records = [b for b in records if b["state"] == state]

    branch = request.args.get("branch")
    if branch:
        records = [b for b in records if b["branch"] == branch]

    trigger = request.args.get("trigger")
    if trigger:
        if trigger not in TRIGGERS:
            raise ValidationError(
                "Invalid trigger filter.",
                errors=[
                    {
                        "field": "trigger",
                        "message": "must be one of: {}".format(
                            ", ".join(sorted(TRIGGERS))
                        ),
                    }
                ],
            )
        records = [b for b in records if b["trigger"] == trigger]

    limit = parse_limit(request.args.get("limit"))
    page, next_cursor = paginate(
        records, limit=limit, cursor=request.args.get("cursor")
    )

    return jsonify(
        {
            "data": [serialize(b) for b in page],
            "pagination": {
                "limit": limit,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
        }
    )


@bp.post("")
def create_build(slug):
    """POST — 201 with Location, idempotent on `Idempotency-Key`.

    POST is not naturally idempotent, so a client whose request times out
    cannot safely retry. The header fixes that: the same key returns the
    original build instead of queueing a second one.
    """
    key = request.headers.get("Idempotency-Key")
    if key:
        existing = store.lookup_idempotency(key)
        if existing:
            build = store.get(existing[0], existing[1])
            response = jsonify(serialize(build))
            response.status_code = 200  # 200, not 201 — nothing was created.
            response.headers["Location"] = serialize(build)["links"]["self"]
            response.headers["ETag"] = etag_for(build)
            response.headers["Idempotent-Replay"] = "true"
            return response

    payload = validate_build_create(request.get_json(silent=True))
    payload["project_id"] = g.project["id"]
    payload["project_slug"] = g.project["slug"]

    build = store.create("builds", payload)
    if key:
        store.remember_idempotency(key, "builds", build["id"])

    response = jsonify(serialize(build))
    response.status_code = 201
    response.headers["Location"] = serialize(build)["links"]["self"]
    response.headers["ETag"] = etag_for(build)
    return response


@bp.get("/<build_id>")
def get_build(slug, build_id):
    """GET one — conditional via `If-None-Match` (304)."""
    build = _get_scoped(build_id)
    etag = etag_for(build)
    if request.headers.get("If-None-Match") == etag:
        # Saves the client re-downloading a body it already has.
        return "", 304, {"ETag": etag}
    response = jsonify(serialize(build))
    response.headers["ETag"] = etag
    return response


@bp.patch("/<build_id>")
def patch_build(slug, build_id):
    """PATCH — partial update, optionally guarded by `If-Match`.

    Only metadata is patchable. `state` is deliberately not: it has side
    effects, so it moves through the transition sub-resource instead.
    """
    build = _get_scoped(build_id)
    expected_version = _parse_if_match(request.headers.get("If-Match"))
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError(
            "Request body must be a JSON object.",
            errors=[{"field": "body", "message": "expected a JSON object"}],
        )
    if "state" in body:
        raise ValidationError(
            "`state` cannot be patched; POST to /transition instead.",
            errors=[{"field": "state", "message": "use the transition action"}],
        )

    changes = {}
    if "branch" in body:
        if not isinstance(body["branch"], str) or not body["branch"].strip():
            raise ValidationError(
                "The request body is invalid.",
                errors=[{"field": "branch", "message": "must be a non-empty string"}],
            )
        changes["branch"] = body["branch"].strip()

    updated = store.update(
        "builds", build_id, changes, expected_version=expected_version
    )
    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


@bp.post("/<build_id>/transition")
def transition_build(slug, build_id):
    """POST a state change as its own sub-resource.

    A state change is an *event* with side effects — it stamps timestamps and
    opens or closes jobs — not a field assignment. Giving it an endpoint keeps
    PATCH free of hidden behavior.
    """
    build = _get_scoped(build_id)
    target = validate_transition(request.get_json(silent=True))
    previous = build["state"]

    if previous == target:
        # A retried webhook must not stamp new timestamps or re-open jobs.
        response = jsonify(serialize(build))
        response.headers["ETag"] = etag_for(build)
        return response

    if previous in TERMINAL_STATES:
        raise ConflictError(
            "Build is already {} and cannot move to {}.".format(previous, target),
            code="invalid_state_transition",
        )

    changes = {"state": target}

    if target == "running":
        changes["started_at"] = utcnow()
        for name in build["jobs"]:
            store.create(
                "jobs",
                {
                    "build_id": build["id"],
                    "project_id": build["project_id"],
                    "name": name,
                    "project_slug": build["project_slug"],
                    "state": "running",
                    "finished_at": None,
                },
            )
    elif target in ("passed", "failed"):
        changes["finished_at"] = utcnow()
        for job in store.list("jobs"):
            if job["build_id"] == build["id"] and job["state"] == "running":
                store.update(
                    "jobs",
                    job["id"],
                    {"state": target, "finished_at": utcnow()},
                )

    updated = store.update("builds", build_id, changes)
    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


def _get_scoped(build_id):
    """Fetch and enforce the project scope.

    A build in another project returns 404, not 403 — 403 would confirm the
    ID exists, which leaks one project's data to another.
    """
    build = store.get("builds", build_id)
    if build["project_id"] != g.project["id"]:
        raise NotFoundError("Build '{}' was not found.".format(build_id))
    return build
