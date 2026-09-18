"""Request-body validation.

Hand-rolled instead of pydantic/marshmallow so the repo installs with just
Flask and every rule is visible in one file. A production service should use a
schema library — see `INTERVIEWER.md`.

The guiding rule: collect *all* field errors and return them together. An API
that fails on the first bad field forces the client into a fix-one-retry loop.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.errors import ValidationError

CONTENT_KINDS = {"post", "comment", "image", "video"}

# The queue a case waits in. `standard` is the default; `priority` is what the
# classifier assigns to content it scored as likely-violating.
QUEUES = {"standard", "priority", "legal"}

# Case states. `escalated` is a real state a reviewer can move a case into when
# it needs a second opinion.
CASE_STATES = {"pending", "in_review", "approved", "removed", "escalated"}

# Outcomes a reviewer may decide. Note these are a subset of CASE_STATES.
DECISIONS = {"approved", "removed", "escalated"}

REASON_CODES = {
    "spam",
    "harassment",
    "violence",
    "nudity",
    "self_harm",
    "misinformation",
    "none",
}

MAX_LABELS = 20
MAX_LABEL_LENGTH = 200
MAX_BODY_LENGTH = 5000


class FieldErrors:
    """Accumulator so one response can report every problem at once."""

    def __init__(self) -> None:
        self.items: List[Dict[str, str]] = []

    def add(self, field: str, message: str) -> None:
        self.items.append({"field": field, "message": message})

    def raise_if_any(self, message: str = "Request validation failed.") -> None:
        if self.items:
            raise ValidationError(message, errors=self.items)


def require_json(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object.")
    return body


def _validate_labels(labels: Any, errors: FieldErrors) -> Optional[List[str]]:
    """Classifier labels, normalized to `key:value`.

    Unbounded label cardinality is the classic cost bug in a system that
    indexes by label, so both the count and the length are capped.
    """
    if labels is None:
        return []
    if not isinstance(labels, list):
        errors.add("labels", "must be an array of strings")
        return None
    if len(labels) > MAX_LABELS:
        errors.add("labels", "at most {} labels allowed".format(MAX_LABELS))
        return None
    cleaned: List[str] = []
    for label in labels:
        if not isinstance(label, str) or not label.strip():
            errors.add("labels", "each label must be a non-empty string")
            return None
        if len(label) > MAX_LABEL_LENGTH:
            errors.add("labels", "label exceeds {} characters".format(MAX_LABEL_LENGTH))
            return None
        cleaned.append(label.strip().lower())
    # Dedupe while preserving order.
    seen = set()
    unique = []
    for label in cleaned:
        if label not in seen:
            seen.add(label)
            unique.append(label)
    return unique


def validate_case_create(body: Any) -> Dict[str, Any]:
    """Validate a manually-opened case (a user report).

    Content submitted by the platform itself arrives through `/v1/submissions`
    instead; this is the path for "a human reported something".
    """
    body = require_json(body)
    errors = FieldErrors()

    external_id = body.get("external_id")
    if not isinstance(external_id, str) or not external_id.strip():
        errors.add("external_id", "required, must be a non-empty string")
    elif len(external_id) > 200:
        errors.add("external_id", "must be at most 200 characters")

    kind = body.get("kind")
    if kind not in CONTENT_KINDS:
        errors.add("kind", "must be one of: {}".format(", ".join(sorted(CONTENT_KINDS))))

    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        errors.add("text", "required, must be a non-empty string")
    elif len(text) > MAX_BODY_LENGTH:
        errors.add("text", "must be at most {} characters".format(MAX_BODY_LENGTH))

    reason = body.get("reason", "none")
    if reason not in REASON_CODES:
        errors.add("reason", "must be one of: {}".format(", ".join(sorted(REASON_CODES))))

    queue = body.get("queue", "standard")
    if queue not in QUEUES:
        errors.add("queue", "must be one of: {}".format(", ".join(sorted(QUEUES))))

    labels = _validate_labels(body.get("labels"), errors)

    # Unknown fields are rejected rather than ignored: a client that sends
    # `raeson` should hear about the typo instead of silently losing the value.
    allowed = {"external_id", "kind", "text", "reason", "queue", "labels"}
    for key in body:
        if key not in allowed:
            errors.add(key, "unknown field")

    errors.raise_if_any()

    return {
        "external_id": external_id.strip(),
        "kind": kind,
        "text": text.strip(),
        "reason": reason,
        "queue": queue,
        "labels": labels or [],
        "state": "pending",
        "decided_by": None,
        "decided_at": None,
        "score": None,
    }


def validate_case_patch(body: Any, current: Dict[str, Any]) -> Dict[str, Any]:
    """PATCH: only the supplied fields change.

    Contrast with PUT, which would replace the whole resource. PATCH is right
    here because a case has server-owned fields (`state`, `version`,
    `decided_at`) a client must not have to echo back — and must not be able
    to write. `state` is deliberately absent from the allowlist: it moves only
    through POST /v1/cases/{id}/decision.
    """
    body = require_json(body)
    errors = FieldErrors()
    changes: Dict[str, Any] = {}

    allowed = {"queue", "reason", "labels"}
    for key in body:
        if key not in allowed:
            errors.add(key, "unknown or immutable field")

    if "queue" in body:
        queue = body["queue"]
        if queue not in QUEUES:
            errors.add("queue", "must be one of: {}".format(", ".join(sorted(QUEUES))))
        else:
            changes["queue"] = queue

    if "reason" in body:
        reason = body["reason"]
        if reason not in REASON_CODES:
            errors.add(
                "reason", "must be one of: {}".format(", ".join(sorted(REASON_CODES)))
            )
        else:
            changes["reason"] = reason

    if "labels" in body:
        labels = _validate_labels(body["labels"], errors)
        if labels is not None:
            changes["labels"] = labels

    errors.raise_if_any()
    if not changes:
        raise ValidationError("Request body must contain at least one field to update.")
    return changes


def validate_decision(body: Any) -> Dict[str, Any]:
    """A reviewer's decision on a case.

    `reviewer` is required and recorded: an unattributed moderation decision
    cannot be audited, and audit is the whole point of a moderation log.
    """
    body = require_json(body)
    errors = FieldErrors()

    decision = body.get("decision")
    if decision not in DECISIONS:
        errors.add("decision", "must be one of: {}".format(", ".join(sorted(DECISIONS))))

    reviewer = body.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        errors.add("reviewer", "required, must be a non-empty string")

    reason = body.get("reason", "none")
    if reason not in REASON_CODES:
        errors.add("reason", "must be one of: {}".format(", ".join(sorted(REASON_CODES))))

    # Removing content without saying why leaves nothing to appeal against.
    if decision == "removed" and reason == "none":
        errors.add("reason", "required when decision is `removed`")

    note = body.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 1000):
        errors.add("note", "must be a string of at most 1000 characters")

    allowed = {"decision", "reviewer", "reason", "note"}
    for key in body:
        if key not in allowed:
            errors.add(key, "unknown field")

    errors.raise_if_any()

    return {
        "decision": decision,
        "reviewer": reviewer.strip(),
        "reason": reason,
        "note": note,
    }


def validate_appeal(body: Any) -> Dict[str, Any]:
    body = require_json(body)
    errors = FieldErrors()

    submitted_by = body.get("submitted_by")
    if not isinstance(submitted_by, str) or not submitted_by.strip():
        errors.add("submitted_by", "required, must be a non-empty string")

    statement = body.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        errors.add("statement", "required, must be a non-empty string")
    elif len(statement) > 2000:
        errors.add("statement", "must be at most 2000 characters")

    allowed = {"submitted_by", "statement"}
    for key in body:
        if key not in allowed:
            errors.add(key, "unknown field")

    errors.raise_if_any()

    return {
        "submitted_by": submitted_by.strip(),
        "statement": statement.strip(),
    }
