"""Request-body validation.

Every validator collects *all* field errors before raising. Returning only the
first one makes a client fix a form one round trip at a time.
"""

from __future__ import annotations

from app.core.errors import ValidationError

BUILD_STATES = {"queued", "running", "passed", "failed", "cancelled"}

# States a build stops in. Nothing transitions out of these.
TERMINAL_STATES = {"passed", "failed", "cancelled"}

TRIGGERS = {"push", "pull_request", "manual", "schedule"}

MAX_BRANCH_LENGTH = 200
MAX_JOBS = 20


def require_json(body):
    if not isinstance(body, dict):
        raise ValidationError(
            "Request body must be a JSON object.",
            errors=[{"field": "body", "message": "expected a JSON object"}],
        )
    return body


def validate_build_create(body):
    """Validate `POST /v1/projects/<slug>/builds`."""
    body = require_json(body)
    errors = []

    branch = body.get("branch")
    if not isinstance(branch, str) or not branch.strip():
        errors.append({"field": "branch", "message": "required, non-empty string"})
    elif len(branch) > MAX_BRANCH_LENGTH:
        errors.append(
            {
                "field": "branch",
                "message": "must be at most {} characters".format(MAX_BRANCH_LENGTH),
            }
        )

    commit = body.get("commit")
    if not isinstance(commit, str) or len(commit.strip()) < 7:
        errors.append(
            {"field": "commit", "message": "required, at least 7 characters"}
        )

    trigger = body.get("trigger", "push")
    if trigger not in TRIGGERS:
        errors.append(
            {
                "field": "trigger",
                "message": "must be one of: {}".format(", ".join(sorted(TRIGGERS))),
            }
        )

    jobs = body.get("jobs", ["build"])
    if not isinstance(jobs, list) or not jobs:
        errors.append({"field": "jobs", "message": "must be a non-empty array"})
    elif len(jobs) > MAX_JOBS:
        errors.append(
            {"field": "jobs", "message": "at most {} jobs".format(MAX_JOBS)}
        )
    elif any(not isinstance(j, str) or not j.strip() for j in jobs):
        errors.append({"field": "jobs", "message": "each job must be a name"})

    if errors:
        raise ValidationError("The request body is invalid.", errors=errors)

    return {
        "branch": branch.strip(),
        "commit": commit.strip(),
        "trigger": trigger,
        "jobs": [j.strip() for j in jobs],
        "state": "queued",
        "started_at": None,
        "finished_at": None,
    }


def validate_transition(body):
    """Validate `POST /v1/projects/<slug>/builds/<id>/transition`."""
    body = require_json(body)
    state = body.get("state")
    if state not in BUILD_STATES:
        raise ValidationError(
            "Invalid target state.",
            errors=[
                {
                    "field": "state",
                    "message": "must be one of: {}".format(
                        ", ".join(sorted(BUILD_STATES))
                    ),
                }
            ],
        )
    return state
