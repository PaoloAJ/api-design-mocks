"""/v1/projects/<slug>/builds/<id>/jobs — read-only, derived from transitions.

Jobs are not created by clients; they are a consequence of a build starting.
Nesting stops at one level: a job's own detail lives at
`/v1/projects/<slug>/jobs/{id}` rather than four segments deep.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.core.errors import NotFoundError
from app.core.pagination import paginate, parse_limit
from app.core.store import store

bp = Blueprint("jobs", __name__)


def serialize_job(job):
    return {
        "id": job["id"],
        "build_id": job["build_id"],
        "name": job["name"],
        "state": job["state"],
        "finished_at": job["finished_at"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "links": {
            "self": "/v1/projects/{}/jobs/{}".format(
                job["project_slug"], job["id"]
            )
        },
    }


@bp.get("/v1/projects/<slug>/builds/<build_id>/jobs")
def list_build_jobs(slug, build_id):
    """The jobs belonging to one build."""
    build = store.get("builds", build_id)
    if build["project_id"] != g.project["id"]:
        raise NotFoundError("Build '{}' was not found.".format(build_id))

    records = [j for j in store.list("jobs") if j["build_id"] == build_id]
    limit = parse_limit(request.args.get("limit"))
    page, next_cursor = paginate(
        records, limit=limit, cursor=request.args.get("cursor")
    )
    return jsonify(
        {
            "data": [serialize_job(j) for j in page],
            "pagination": {
                "limit": limit,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
        }
    )


@bp.get("/v1/projects/<slug>/jobs/<job_id>")
def get_job(slug, job_id):
    """One job by keyed lookup — never by scanning the build's job list.

    Scoped through the project like everything else: a job belonging to
    another project is a 404, not a 403.
    """
    job = store.get("jobs", job_id)
    if job["project_id"] != g.project["id"]:
        raise NotFoundError("Job '{}' was not found.".format(job_id))
    return jsonify(serialize_job(job))
