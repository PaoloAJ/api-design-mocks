"""Template routes: list and update.

Templates are the slow-moving resource — a handful per account, edited rarely,
read on every send. There is deliberately no create and no delete endpoint:
templates are provisioned out of band, and deleting one would orphan the
messages that reference it.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.errors import ValidationError
from app.pagination import page_response, parse_limit
from app.store import parse_etag, store
from app.validation import validate_template_patch

bp = Blueprint("templates", __name__, url_prefix="/v1/templates")


def serialize(template):
    return {
        "id": template["id"],
        "name": template["name"],
        "subject": template["subject"],
        "body": template["body"],
        "created_at": template["created_at"],
        "updated_at": template["updated_at"],
        "version": template["version"],
    }


@bp.get("")
def list_templates():
    records = store.list("templates", account_id=g.account["id"])
    return jsonify(
        page_response(
            records,
            serialize,
            limit=parse_limit(request.args.get("limit")),
            cursor=request.args.get("cursor"),
        )
    )


@bp.patch("/<template_id>")
def update_template(template_id):
    """Partial update, guarded by `If-Match`.

    Required, not optional: templates are edited by humans in a dashboard, and
    two people saving the same template a second apart should get a 409 rather
    than one silently losing their edit. The send path takes the opposite
    stance because workers retry automatically.
    """
    template = store.get("templates", template_id, account_id=g.account["id"])

    if_match = request.headers.get("If-Match")
    if not if_match:
        raise ValidationError(
            "If-Match is required. Read the template and send its ETag back.",
            code="precondition_required",
            status_code=428,
        )
    try:
        expected_version = parse_etag(if_match)
    except ValueError:
        raise ValidationError(
            "Malformed If-Match header. Pass the ETag from a prior read.",
            errors=[{"field": "If-Match", "message": "not a version tag"}],
        )

    changes = validate_template_patch(request.get_json(silent=True))

    updated = store.update(
        "templates",
        template_id,
        changes,
        expected_version=expected_version,
        account_id=g.account["id"],
    )
    response = jsonify(serialize(updated))
    response.headers["ETag"] = 'W/"{}"'.format(updated["version"])
    return response
