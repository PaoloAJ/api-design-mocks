"""Request-body validation.

Hand-rolled instead of pydantic/marshmallow, same rationale as mock-1: the
repo installs with just Flask and every rule is visible in one place. A
production service should use a schema library.

The guiding rule, unchanged from mock-1: collect *all* field errors and
return them together. An API that fails on the first bad field forces the
client into a fix-one-retry loop.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.errors import ValidationError

# sev1 is the most severe. Ordered (not just a set) because "escalate" means
# "move toward index 0" — see pr_review/ for the endpoint that does this, and
# what it gets wrong.
SEVERITY_ORDER = ["sev1", "sev2", "sev3", "sev4"]
SEVERITIES = set(SEVERITY_ORDER)

STATUSES = {"triggered", "acknowledged", "investigating", "monitoring", "resolved"}

MAX_TAGS = 20
MAX_TAG_LENGTH = 200


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


def _validate_tags(tags: Any, errors: FieldErrors) -> Optional[List[str]]:
    if tags is None:
        return []
    if not isinstance(tags, list):
        errors.add("tags", "must be an array of strings")
        return None
    if len(tags) > MAX_TAGS:
        errors.add("tags", "at most {} tags allowed".format(MAX_TAGS))
        return None
    cleaned: List[str] = []
    for tag in tags:
        if not isinstance(tag, str) or not tag.strip():
            errors.add("tags", "each tag must be a non-empty string")
            return None
        if len(tag) > MAX_TAG_LENGTH:
            errors.add("tags", "tag exceeds {} characters".format(MAX_TAG_LENGTH))
            return None
        cleaned.append(tag.strip().lower())
    # Dedupe while preserving order.
    seen = set()
    unique = []
    for tag in cleaned:
        if tag not in seen:
            seen.add(tag)
            unique.append(tag)
    return unique


def validate_incident_create(body: Any) -> Dict[str, Any]:
    body = require_json(body)
    errors = FieldErrors()

    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.add("title", "required, must be a non-empty string")
    elif len(title) > 200:
        errors.add("title", "must be at most 200 characters")

    severity = body.get("severity")
    if severity not in SEVERITIES:
        errors.add(
            "severity", "must be one of: {}".format(", ".join(SEVERITY_ORDER))
        )

    service_id = body.get("service_id")
    if not isinstance(service_id, str) or not service_id.strip():
        errors.add("service_id", "required, must be a non-empty string")

    summary = body.get("summary")
    if summary is not None and (not isinstance(summary, str) or len(summary) > 2000):
        errors.add("summary", "must be a string of at most 2000 characters")

    commander = body.get("commander")
    if commander is not None and (
        not isinstance(commander, str) or not commander.strip()
    ):
        errors.add("commander", "must be a non-empty string")

    tags = _validate_tags(body.get("tags"), errors)

    # Unknown fields are rejected rather than ignored: a client that sends
    # `sevrity` should hear about the typo instead of silently losing it.
    allowed = {"title", "severity", "service_id", "summary", "commander", "tags"}
    for key in body:
        if key not in allowed:
            errors.add(key, "unknown field")

    errors.raise_if_any()

    return {
        "title": title.strip(),
        "severity": severity,
        "service_id": service_id.strip(),
        "summary": summary.strip() if isinstance(summary, str) else "",
        "commander": commander.strip() if isinstance(commander, str) else None,
        "tags": tags or [],
        "status": "triggered",
        "responders": [],
        "resolved_at": None,
    }


def validate_incident_patch(body: Any) -> Dict[str, Any]:
    """PATCH: only the supplied fields change.

    `severity` and `status` are deliberately not editable here. `status` gets
    its own endpoint because the transition has side effects (see
    `transition_status`); `severity` has *no* write path at all in this
    codebase — see `pr_review/` for the PR that adds one, and what it gets
    wrong by not following this file's conventions.
    """
    body = require_json(body)
    errors = FieldErrors()
    changes: Dict[str, Any] = {}

    allowed = {"title", "summary", "tags", "commander"}
    for key in body:
        if key not in allowed:
            errors.add(key, "unknown or immutable field")

    if "title" in body:
        title = body["title"]
        if not isinstance(title, str) or not title.strip():
            errors.add("title", "must be a non-empty string")
        else:
            changes["title"] = title.strip()

    if "summary" in body:
        summary = body["summary"]
        if not isinstance(summary, str) or len(summary) > 2000:
            errors.add("summary", "must be a string of at most 2000 characters")
        else:
            changes["summary"] = summary.strip()

    if "tags" in body:
        tags = _validate_tags(body["tags"], errors)
        if tags is not None:
            changes["tags"] = tags

    if "commander" in body:
        commander = body["commander"]
        if commander is not None and (
            not isinstance(commander, str) or not commander.strip()
        ):
            errors.add("commander", "must be a non-empty string or null")
        else:
            changes["commander"] = (
                commander.strip() if isinstance(commander, str) else None
            )

    errors.raise_if_any()
    if not changes:
        raise ValidationError(
            "Request body must contain at least one field to update."
        )
    return changes


def validate_status_transition(body: Any) -> str:
    body = require_json(body)
    status = body.get("status")
    if status not in STATUSES:
        raise ValidationError(
            "Invalid status.",
            errors=[
                {
                    "field": "status",
                    "message": "must be one of: {}".format(
                        ", ".join(sorted(STATUSES))
                    ),
                }
            ],
        )
    return status


def validate_responder(body: Any) -> str:
    body = require_json(body)
    handle = body.get("handle")
    if not isinstance(handle, str) or not handle.strip():
        raise ValidationError(
            "`handle` is required.",
            errors=[{"field": "handle", "message": "required, non-empty string"}],
        )
    if len(handle) > 100:
        raise ValidationError(
            "`handle` must be at most 100 characters.",
            errors=[{"field": "handle", "message": "too long"}],
        )
    return handle.strip()


def validate_timeline_note(body: Any) -> Dict[str, str]:
    body = require_json(body)
    errors = FieldErrors()

    author = body.get("author")
    if not isinstance(author, str) or not author.strip():
        errors.add("author", "required, non-empty string")

    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        errors.add("message", "required, non-empty string")
    elif len(message) > 4000:
        errors.add("message", "must be at most 4000 characters")

    errors.raise_if_any()
    return {"author": author.strip(), "message": message.strip()}
