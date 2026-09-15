"""Request-body validation.

Hand-rolled instead of pydantic/marshmallow so the repo installs with just
Flask and every rule is visible. A production service should use a schema
library — see `INTERVIEWER.md`.

The guiding rule: collect *all* field errors and return them together. An API
that fails on the first bad field forces the client into a fix-one-retry loop.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.core.errors import ValidationError

MONITOR_TYPES = {"metric", "log", "apm", "synthetic"}
MONITOR_STATUSES = {"ok", "warn", "alert", "no_data"}
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
        # Normalize to `key:value` lowercase so `Env:Prod` and `env:prod` are
        # the same tag. Unbounded tag cardinality is the classic observability
        # cost bug.
        cleaned.append(tag.strip().lower())
    # Dedupe while preserving order.
    seen = set()
    unique = []
    for tag in cleaned:
        if tag not in seen:
            seen.add(tag)
            unique.append(tag)
    return unique


def validate_monitor_create(body: Any) -> Dict[str, Any]:
    body = require_json(body)
    errors = FieldErrors()

    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.add("name", "required, must be a non-empty string")
    elif len(name) > 200:
        errors.add("name", "must be at most 200 characters")

    monitor_type = body.get("type")
    if monitor_type not in MONITOR_TYPES:
        errors.add(
            "type", "must be one of: {}".format(", ".join(sorted(MONITOR_TYPES)))
        )

    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        errors.add("query", "required, must be a non-empty string")

    thresholds = body.get("thresholds", {})
    if not isinstance(thresholds, dict):
        errors.add("thresholds", "must be an object")
        thresholds = {}
    else:
        for level in ("warning", "critical"):
            value = thresholds.get(level)
            if value is not None and not isinstance(value, (int, float)):
                errors.add("thresholds.{}".format(level), "must be a number")
        warning = thresholds.get("warning")
        critical = thresholds.get("critical")
        if isinstance(warning, (int, float)) and isinstance(critical, (int, float)):
            if warning >= critical:
                errors.add(
                    "thresholds.warning", "must be less than thresholds.critical"
                )

    tags = _validate_tags(body.get("tags"), errors)

    # Unknown fields are rejected rather than ignored: a client that sends
    # `nmae` should hear about the typo instead of silently losing the value.
    allowed = {"name", "type", "query", "thresholds", "tags", "enabled"}
    for key in body:
        if key not in allowed:
            errors.add(key, "unknown field")

    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.add("enabled", "must be a boolean")

    errors.raise_if_any()

    return {
        "name": name.strip(),
        "type": monitor_type,
        "query": query.strip(),
        "thresholds": {
            "warning": thresholds.get("warning"),
            "critical": thresholds.get("critical"),
        },
        "tags": tags or [],
        "enabled": enabled,
        "status": "ok",
    }


def validate_monitor_patch(body: Any, current: Dict[str, Any]) -> Dict[str, Any]:
    """PATCH: only the supplied fields change.

    Contrast with PUT, which would replace the whole resource. PATCH is the
    right verb here because monitors have server-owned fields (`status`,
    `version`) a client must not have to echo back.
    """
    body = require_json(body)
    errors = FieldErrors()
    changes: Dict[str, Any] = {}

    allowed = {"name", "query", "thresholds", "tags", "enabled"}
    for key in body:
        if key not in allowed:
            errors.add(key, "unknown or immutable field")

    if "name" in body:
        name = body["name"]
        if not isinstance(name, str) or not name.strip():
            errors.add("name", "must be a non-empty string")
        else:
            changes["name"] = name.strip()

    if "query" in body:
        query = body["query"]
        if not isinstance(query, str) or not query.strip():
            errors.add("query", "must be a non-empty string")
        else:
            changes["query"] = query.strip()

    if "thresholds" in body:
        thresholds = body["thresholds"]
        if not isinstance(thresholds, dict):
            errors.add("thresholds", "must be an object")
        else:
            merged = dict(current.get("thresholds") or {})
            merged.update(
                {k: v for k, v in thresholds.items() if k in ("warning", "critical")}
            )
            for level in ("warning", "critical"):
                value = merged.get(level)
                if value is not None and not isinstance(value, (int, float)):
                    errors.add("thresholds.{}".format(level), "must be a number")
            changes["thresholds"] = merged

    if "tags" in body:
        tags = _validate_tags(body["tags"], errors)
        if tags is not None:
            changes["tags"] = tags

    if "enabled" in body:
        enabled = body["enabled"]
        if not isinstance(enabled, bool):
            errors.add("enabled", "must be a boolean")
        else:
            changes["enabled"] = enabled

    errors.raise_if_any()
    if not changes:
        raise ValidationError("Request body must contain at least one field to update.")
    return changes


def validate_status_transition(body: Any) -> str:
    body = require_json(body)
    status = body.get("status")
    if status not in MONITOR_STATUSES:
        raise ValidationError(
            "Invalid status.",
            errors=[
                {
                    "field": "status",
                    "message": "must be one of: {}".format(
                        ", ".join(sorted(MONITOR_STATUSES))
                    ),
                }
            ],
        )
    return status
