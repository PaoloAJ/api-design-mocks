"""/v1/submissions — bulk content ingest.

A deliberately different shape from the CRUD resources. Ingest endpoints are
write-heavy, batched, and latency-sensitive, which changes the design:

  * Accepts an array so one request carries many items (per-item HTTP overhead
    would dominate at real volume).
  * Returns 202 Accepted, not 201: the content is durably queued, but the
    classifier has not scored it and no case is reviewable yet. Promising 201
    would mean promising a case exists at a URL the client could GET.
  * Reports per-item results so one malformed item does not reject the batch.

The classifier itself is NOT in this repo. Ingest records the content and
stops; scoring and queue assignment happen elsewhere and arrive back as a
`PATCH`. That gap is deliberate — see INTERVIEWER.md.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.core.errors import ValidationError
from app.core.store import store
from app.core.validation import CONTENT_KINDS, MAX_BODY_LENGTH, require_json

bp = Blueprint("submissions", __name__, url_prefix="/v1/submissions")

MAX_ITEMS_PER_REQUEST = 500


@bp.post("")
def submit_content():
    body = require_json(request.get_json(silent=True))
    items = body.get("items")
    if not isinstance(items, list):
        raise ValidationError(
            "`items` must be an array.",
            errors=[{"field": "items", "message": "required array"}],
        )
    if not items:
        raise ValidationError(
            "`items` must contain at least one item.",
            errors=[{"field": "items", "message": "must not be empty"}],
        )
    if len(items) > MAX_ITEMS_PER_REQUEST:
        # 413 rather than 400: the request is well-formed but too large. A hard
        # cap keeps one publisher from monopolizing a worker.
        raise ValidationError(
            "At most {} items per request.".format(MAX_ITEMS_PER_REQUEST),
            code="payload_too_large",
            status_code=413,
            errors=[{"field": "items", "message": "too many items"}],
        )

    accepted = []
    rejected = []

    for index, item in enumerate(items):
        error = _validate_item(item)
        if error is not None:
            # Index is included so the client can map the failure back to the
            # exact element it sent.
            rejected.append({"index": index, "reason": error})
            continue

        # Accepting the same external_id twice is not an error — a publisher
        # retrying a batch should not get a 409 for the half that landed. We
        # report the existing case's id so the retry is a no-op the client can
        # reconcile against.
        existing = store.find_one(
            "items", platform_id=g.platform_id, external_id=item["external_id"]
        )
        if existing is not None:
            accepted.append({"index": index, "item_id": existing["id"], "duplicate": True})
            continue

        record = store.create(
            "items",
            {
                "platform_id": g.platform_id,
                "external_id": item["external_id"].strip(),
                "kind": item["kind"],
                "text": item["text"].strip(),
                # Set by the classifier, which runs after this response is sent.
                "score": None,
                "case_id": None,
            },
        )
        accepted.append({"index": index, "item_id": record["id"], "duplicate": False})

    status = 202 if accepted else 400
    return (
        jsonify(
            {
                "accepted": len(accepted),
                "rejected": len(rejected),
                "items": accepted,
                "errors": rejected,
            }
        ),
        status,
    )


def _validate_item(item):
    """Return an error string, or None if the item is acceptable.

    Per-item validation returns rather than raises: one bad item must not cost
    the client the whole batch.
    """
    if not isinstance(item, dict):
        return "must be an object"

    external_id = item.get("external_id")
    if not isinstance(external_id, str) or not external_id.strip():
        return "`external_id` is required"
    if len(external_id) > 200:
        return "`external_id` exceeds 200 characters"

    kind = item.get("kind")
    if kind not in CONTENT_KINDS:
        return "`kind` must be one of: {}".format(", ".join(sorted(CONTENT_KINDS)))

    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        return "`text` is required"
    if len(text) > MAX_BODY_LENGTH:
        return "`text` exceeds {} characters".format(MAX_BODY_LENGTH)

    return None
