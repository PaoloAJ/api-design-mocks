"""/v1/series — bulk metric ingest.

A deliberately different shape from the CRUD resources. Ingest endpoints are
write-heavy, batched, and latency-sensitive, which changes the design:

  * Accepts an array so one request carries many points (per-point HTTP
    overhead would dominate at real volume).
  * Returns 202 Accepted, not 201: the write is durable enough to acknowledge
    but downstream aggregation happens asynchronously. Promising 201 would
    mean promising the point is already queryable.
  * Reports per-item results so one malformed point does not reject the batch.
"""

from __future__ import annotations

import time

from flask import Blueprint, g, jsonify, request

from app.core.errors import ValidationError
from app.core.validation import MAX_TAGS, require_json

bp = Blueprint("metrics", __name__, url_prefix="/v1/series")

MAX_POINTS_PER_REQUEST = 1000
MAX_METRIC_NAME_LENGTH = 200
# Guards against a timestamp far enough in the future to corrupt rollups.
MAX_FUTURE_SKEW_SECONDS = 600
MAX_PAST_AGE_SECONDS = 60 * 60 * 24

# Stand-in for a time-series backend. A real one writes to Kafka, then to a
# columnar TSDB; nothing about the HTTP contract changes.
_ingested = []


@bp.post("")
def submit_series():
    body = require_json(request.get_json(silent=True))
    series = body.get("series")
    if not isinstance(series, list):
        raise ValidationError(
            "`series` must be an array.",
            errors=[{"field": "series", "message": "required array"}],
        )
    if not series:
        raise ValidationError(
            "`series` must contain at least one item.",
            errors=[{"field": "series", "message": "must not be empty"}],
        )
    if len(series) > MAX_POINTS_PER_REQUEST:
        # 413 rather than 400: the request is well-formed but too large. A
        # hard cap keeps one client from monopolizing a worker.
        raise ValidationError(
            "At most {} series per request.".format(MAX_POINTS_PER_REQUEST),
            code="payload_too_large",
            status_code=413,
            errors=[{"field": "series", "message": "too many items"}],
        )

    now = time.time()
    accepted = 0
    rejected = []

    for index, item in enumerate(series):
        error = _validate_point(item, now)
        if error is not None:
            # Index is included so the client can map the failure back to the
            # exact element it sent.
            rejected.append({"index": index, "reason": error})
            continue
        _ingested.append({"org_id": g.org_id, "metric": item["metric"]})
        accepted += 1

    status = 202 if accepted else 400
    return (
        jsonify(
            {
                "accepted": accepted,
                "rejected": len(rejected),
                "errors": rejected,
            }
        ),
        status,
    )


def _validate_point(item, now):
    if not isinstance(item, dict):
        return "must be an object"

    metric = item.get("metric")
    if not isinstance(metric, str) or not metric.strip():
        return "`metric` is required"
    if len(metric) > MAX_METRIC_NAME_LENGTH:
        return "`metric` exceeds {} characters".format(MAX_METRIC_NAME_LENGTH)

    points = item.get("points")
    if not isinstance(points, list) or not points:
        return "`points` must be a non-empty array"

    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            return "each point must be [timestamp, value]"
        timestamp, value = point
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            return "point timestamp must be a unix epoch number"
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return "point value must be a number"
        if timestamp > now + MAX_FUTURE_SKEW_SECONDS:
            return "point timestamp is too far in the future"
        if timestamp < now - MAX_PAST_AGE_SECONDS:
            return "point timestamp is too far in the past"

    tags = item.get("tags", [])
    if not isinstance(tags, list):
        return "`tags` must be an array"
    if len(tags) > MAX_TAGS:
        # Cardinality control: every distinct tag combination is a separate
        # time series to store and index.
        return "at most {} tags allowed".format(MAX_TAGS)
    for tag in tags:
        if not isinstance(tag, str) or ":" not in tag:
            return "each tag must be a 'key:value' string"

    return None
