"""Delivery-event ingest and readback.

This is where the provider talks back to us. It is not CRUD, and it should not
be read as CRUD: one request carries many events, each can fail on its own,
and the response says what happened to each.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.errors import ValidationError
from app.pagination import page_response, parse_limit
from app.store import store

bp = Blueprint("events", __name__, url_prefix="/v1/events")

MAX_BATCH = 100

EVENT_TYPES = ("message.delivered", "message.bounced", "message.deferred")


def serialize(event):
    return {
        "id": event["id"],
        "message_id": event["message_id"],
        "type": event["type"],
        "detail": event["detail"],
        "created_at": event["created_at"],
    }


@bp.post("")
def ingest_events():
    """Accept a batch of provider callbacks.

    Returns `202` with a per-item result rather than `201`, because a batch is
    rarely all-or-nothing: rejecting 100 good events because one had a bad
    message ID would make the provider replay the whole batch. Each result
    carries its `index` so the caller can line failures up with what it sent
    without guessing at ordering.

    Accepting a partial batch is a real tradeoff. The alternative — reject the
    whole thing on any bad item — is simpler to reason about and would be the
    right call if these events were financial.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValidationError(
            "Request body must be an object with an `events` array.",
            errors=[{"field": "events", "message": "expected an array"}],
        )

    batch = payload["events"]
    if not batch:
        raise ValidationError(
            "`events` must not be empty.",
            errors=[{"field": "events", "message": "must contain at least one event"}],
        )
    if len(batch) > MAX_BATCH:
        raise ValidationError(
            "A batch may carry at most {} events.".format(MAX_BATCH),
            errors=[{"field": "events", "message": "batch too large"}],
        )

    accepted, rejected = [], []

    for index, item in enumerate(batch):
        if not isinstance(item, dict):
            rejected.append(
                {"index": index, "code": "invalid", "message": "expected an object"}
            )
            continue

        message_id = item.get("message_id")
        event_type = item.get("type")

        if not message_id:
            rejected.append(
                {
                    "index": index,
                    "code": "validation_error",
                    "message": "`message_id` is required",
                }
            )
            continue
        if event_type not in EVENT_TYPES:
            rejected.append(
                {
                    "index": index,
                    "code": "validation_error",
                    "message": "`type` must be one of: {}".format(
                        ", ".join(EVENT_TYPES)
                    ),
                }
            )
            continue

        # Keyed lookup scoped to the account. An event naming another
        # account's message is rejected exactly like an unknown one.
        message = store.find_one(
            "messages", id=message_id, account_id=g.account["id"]
        )
        if message is None:
            rejected.append(
                {
                    "index": index,
                    "code": "not_found",
                    "message": "unknown message '{}'".format(message_id),
                }
            )
            continue

        event = store.create(
            "events",
            {
                "account_id": g.account["id"],
                "message_id": message_id,
                "type": event_type,
                "detail": item.get("detail"),
            },
        )
        accepted.append({"index": index, "id": event["id"]})

    # NOTE: recording an event does not move the message it names. Nothing in
    # this service applies an event to a message's status — a worker calls
    # /transition separately. That gap is deliberate.
    response = jsonify(
        {
            "accepted": len(accepted),
            "rejected": len(rejected),
            "results": accepted,
            "errors": rejected,
        }
    )
    response.status_code = 202
    return response


@bp.get("")
def list_events():
    """Read back the caller's events, newest first."""
    records = store.list("events", account_id=g.account["id"])

    # Filter before paginating, never after. See SPEC rule 2.
    message_id = request.args.get("message_id")
    if message_id:
        records = [r for r in records if r["message_id"] == message_id]

    return jsonify(
        page_response(
            records,
            serialize,
            limit=parse_limit(request.args.get("limit")),
            cursor=request.args.get("cursor"),
        )
    )
