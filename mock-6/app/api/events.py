"""/v1/events — bulk ingest of runner events.

Deliberately a different shape from the CRUD resources. Build runners report
progress in batches, which changes the design:

  * Accepts an array, so one request carries many events. Per-event HTTP
    overhead would dominate at real volume.
  * Returns 202 Accepted, not 201: the batch is durable enough to acknowledge,
    but nothing has been applied to a build yet. Promising 201 would promise
    the caller that a GET would already reflect it.
  * Reports a per-item result, so one malformed event does not reject the
    whole batch. Each error carries the index the client sent.
"""

from __future__ import annotations

import time

from flask import Blueprint, jsonify, request

from app.core.errors import ValidationError
from app.core.validation import require_json

bp = Blueprint("events", __name__, url_prefix="/v1/events")

MAX_EVENTS_PER_REQUEST = 500
EVENT_KINDS = {"job_started", "job_finished", "log_chunk"}
# Guards against a clock-skewed runner corrupting build timings.
MAX_FUTURE_SKEW_SECONDS = 300
MAX_PAST_AGE_SECONDS = 60 * 60 * 6

# Stand-in for the queue this hands off to. A real service writes to Kafka and
# a worker applies the events to builds; nothing about the HTTP contract
# changes when it does.
_queued = []


@bp.post("")
def submit_events():
    body = require_json(request.get_json(silent=True))
    events = body.get("events")
    if not isinstance(events, list):
        raise ValidationError(
            "`events` must be an array.",
            errors=[{"field": "events", "message": "required array"}],
        )
    if not events:
        raise ValidationError(
            "`events` must contain at least one item.",
            errors=[{"field": "events", "message": "must not be empty"}],
        )
    if len(events) > MAX_EVENTS_PER_REQUEST:
        # 413, not 400: the request is well formed, just too large. A hard cap
        # keeps one runner from monopolizing a worker.
        raise ValidationError(
            "At most {} events per request.".format(MAX_EVENTS_PER_REQUEST),
            code="payload_too_large",
            status_code=413,
            errors=[{"field": "events", "message": "too many items"}],
        )

    now = time.time()
    accepted = 0
    rejected = []

    for index, item in enumerate(events):
        reason = _validate_event(item, now)
        if reason is not None:
            # The index lets the client map a failure back to what it sent.
            rejected.append({"index": index, "reason": reason})
            continue
        _queued.append({"build_id": item["build_id"], "kind": item["kind"]})
        accepted += 1

    # All-or-nothing on an empty accept: a batch where nothing landed is a
    # client error, not a partial success.
    status = 202 if accepted else 400
    return (
        jsonify({"accepted": accepted, "rejected": len(rejected), "errors": rejected}),
        status,
    )


def _validate_event(item, now):
    if not isinstance(item, dict):
        return "must be an object"

    build_id = item.get("build_id")
    if not isinstance(build_id, str) or not build_id.strip():
        return "`build_id` is required"

    kind = item.get("kind")
    if kind not in EVENT_KINDS:
        return "`kind` must be one of: {}".format(", ".join(sorted(EVENT_KINDS)))

    timestamp = item.get("timestamp")
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
        return "`timestamp` must be a unix epoch number"
    if timestamp > now + MAX_FUTURE_SKEW_SECONDS:
        return "`timestamp` is too far in the future"
    if timestamp < now - MAX_PAST_AGE_SECONDS:
        return "`timestamp` is too far in the past"

    return None
