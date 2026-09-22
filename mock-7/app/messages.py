"""Message routes: send, list, read, transition.

This is the spine of the service. A send is accepted and queued (`202`), a
worker later reports what the provider did, and the message reaches a terminal
state. Nothing here talks to an SMTP provider — see `transition_message` for
where that boundary sits.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.errors import ValidationError
from app.pagination import page_response, parse_limit
from app.store import parse_etag, store
from app.validation import TERMINAL_STATUSES, validate_message, validate_transition

bp = Blueprint("messages", __name__, url_prefix="/v1/messages")


def serialize(message):
    """Wire shape, built field by field.

    Never `return record`: the stored dict grows internal fields, and a
    spread would leak each new one into the public contract by default.
    """
    return {
        "id": message["id"],
        "to": message["to"],
        "subject": message["subject"],
        "template_id": message["template_id"],
        "status": message["status"],
        "attempts": message["attempts"],
        "last_error": message["last_error"],
        "created_at": message["created_at"],
        "updated_at": message["updated_at"],
        "version": message["version"],
    }


def etag_for(message):
    """Weak ETag over the version counter.

    The version is what changes on every write, so it is the cheapest honest
    validator we can offer. Weak (`W/`) because it tracks semantic version,
    not a byte-for-byte hash of the body.
    """
    return 'W/"{}"'.format(message["version"])


@bp.post("")
def send_message():
    """Accept a message for delivery.

    Returns `202`, not `201`: the resource exists, but the *work* it describes
    has not happened. The caller polls or waits for a webhook. Returning `200`
    here would imply the mail was delivered, which we cannot know yet.
    """
    payload = validate_message(request.get_json(silent=True))

    # A template is required, and it must belong to the caller. Scoping the
    # lookup to the account is what stops one customer sending with another
    # customer's template.
    template = store.get(
        "templates", payload["template_id"], account_id=g.account["id"]
    )

    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing = store.lookup_idempotency(idempotency_key)
        if existing:
            kind, resource_id = existing
            message = store.get(kind, resource_id, account_id=g.account["id"])
            response = jsonify(serialize(message))
            response.status_code = 202
            response.headers["ETag"] = etag_for(message)
            response.headers["Idempotent-Replay"] = "true"
            return response

    payload["account_id"] = g.account["id"]
    payload["template_name"] = template["name"]
    message = store.create("messages", payload)

    if idempotency_key:
        store.remember_idempotency(idempotency_key, "messages", message["id"])

    response = jsonify(serialize(message))
    response.status_code = 202
    response.headers["ETag"] = etag_for(message)
    response.headers["Location"] = "/v1/messages/{}".format(message["id"])
    return response


@bp.get("")
def list_messages():
    """List the caller's messages, newest first.

    Filtering happens before pagination. Filtering a page after slicing it
    returns short pages and a cursor that walks the wrong rows.
    """
    records = store.list("messages", account_id=g.account["id"])

    status = request.args.get("status")
    if status:
        records = [r for r in records if r["status"] == status]

    to = request.args.get("to")
    if to:
        records = [r for r in records if r["to"] == to]

    return jsonify(
        page_response(
            records,
            serialize,
            limit=parse_limit(request.args.get("limit")),
            cursor=request.args.get("cursor"),
        )
    )


@bp.get("/<message_id>")
def get_message(message_id):
    """Fetch one message by ID.

    Keyed lookup, scoped to the account. This handler must never load the
    collection — see SPEC rule 1.
    """
    message = store.get("messages", message_id, account_id=g.account["id"])
    response = jsonify(serialize(message))
    response.headers["ETag"] = etag_for(message)
    return response


@bp.post("/<message_id>/transition")
def transition_message(message_id):
    """Move a message through the delivery state machine.

    An action sub-resource rather than `PATCH /messages/{id}` with a status
    field, because a transition is not a field write: it bumps the attempt
    counter, records provider errors, and is the hook a notification would
    hang off. `PATCH` implies "set this value"; this endpoint means "try to
    make this happen, and refuse if the machine says no".

    `If-Match` is optional here but enforced when present: two workers racing
    to report on the same message should not silently overwrite each other.
    """
    message = store.get("messages", message_id, account_id=g.account["id"])

    if_match = request.headers.get("If-Match")
    expected_version = None
    if if_match:
        try:
            expected_version = parse_etag(if_match)
        except ValueError:
            raise ValidationError(
                "Malformed If-Match header. Pass the ETag from a prior read.",
                errors=[{"field": "If-Match", "message": "not a version tag"}],
            )

    body = request.get_json(silent=True)
    target = validate_transition(body, message["status"])

    changes = {"status": target}

    if target == "sending":
        changes["attempts"] = message["attempts"] + 1
    elif target == "delivered":
        changes["last_error"] = None
    elif target == "bounced":
        reason = (body or {}).get("reason")
        if not reason:
            raise ValidationError(
                "A bounce must say why.",
                errors=[{"field": "reason", "message": "required when bouncing"}],
            )
        changes["last_error"] = reason

    updated = store.update(
        "messages",
        message_id,
        changes,
        expected_version=expected_version,
        account_id=g.account["id"],
    )

    if target in TERMINAL_STATUSES:
        store.create(
            "events",
            {
                "account_id": g.account["id"],
                "message_id": message_id,
                "type": "message.{}".format(target),
                "detail": updated["last_error"],
            },
        )

    response = jsonify(serialize(updated))
    response.headers["ETag"] = etag_for(updated)
    return response
