"""Request-body validation and the delivery state machine.

Two rules drive everything here:

Collect every field error, never stop at the first. A client fixing a form one
round-trip per mistake is a bad API; one 400 listing four bad fields is one
round-trip.

Transitions live in a table, not in `if` chains scattered across handlers.
`ALLOWED_TRANSITIONS` is the whole contract: if an edge is not in it, it
cannot happen, and adding a state means editing one dict.
"""

from __future__ import annotations

import re

from app.errors import ValidationError

# A message is queued, then a worker attempts it, then it ends. `bounced` and
# `delivered` are terminal: the provider has spoken and we do not re-litigate.
STATUSES = ("queued", "sending", "delivered", "bounced", "deferred")

TERMINAL_STATUSES = ("delivered", "bounced")

ALLOWED_TRANSITIONS = {
    "queued": ("sending",),
    # `deferred` is a soft failure (greylisting, mailbox full). The provider
    # will retry, so it goes back to the queue rather than ending the message.
    "sending": ("delivered", "bounced", "deferred"),
    "deferred": ("sending",),
    "delivered": (),
    "bounced": (),
}

# Deliberately permissive: full RFC 5322 is not the point, and a stricter
# regex rejects addresses that are actually valid.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _require_object(payload):
    if not isinstance(payload, dict):
        raise ValidationError(
            "Request body must be a JSON object.",
            errors=[{"field": "body", "message": "expected an object"}],
        )
    return payload


def validate_message(payload):
    """Validate a send request, collecting every problem before raising."""
    payload = _require_object(payload)
    errors = []

    to = payload.get("to")
    if not to:
        errors.append({"field": "to", "message": "required"})
    elif not isinstance(to, str) or not EMAIL_RE.match(to):
        errors.append({"field": "to", "message": "must be an email address"})

    subject = payload.get("subject")
    if not subject:
        errors.append({"field": "subject", "message": "required"})
    elif not isinstance(subject, str):
        errors.append({"field": "subject", "message": "must be a string"})
    elif len(subject) > 200:
        errors.append({"field": "subject", "message": "must be <= 200 chars"})

    template_id = payload.get("template_id")
    if not template_id:
        errors.append({"field": "template_id", "message": "required"})
    elif not isinstance(template_id, str):
        errors.append({"field": "template_id", "message": "must be a string"})

    variables = payload.get("variables", {})
    if not isinstance(variables, dict):
        errors.append({"field": "variables", "message": "must be an object"})

    if errors:
        raise ValidationError("The request body failed validation.", errors=errors)

    return {
        "to": to,
        "subject": subject,
        "template_id": template_id,
        "variables": variables,
        "status": "queued",
        "attempts": 0,
        "last_error": None,
    }


def validate_template_patch(payload):
    """Validate a partial template update.

    Absent means "leave alone"; present-but-empty is a mistake worth a 400.
    A body with no known field at all is also an error — silently doing
    nothing and returning 200 would hide a client-side typo.
    """
    payload = _require_object(payload)
    errors, changes = [], {}

    for field in ("name", "subject", "body"):
        if field not in payload:
            continue
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            errors.append({"field": field, "message": "must be a non-empty string"})
        else:
            changes[field] = value

    if errors:
        raise ValidationError("The request body failed validation.", errors=errors)
    if not changes:
        raise ValidationError(
            "No updatable fields supplied.",
            errors=[{"field": "body", "message": "expected name, subject or body"}],
        )
    return changes


def validate_transition(payload, current_status):
    """Validate a requested status change against the transition table."""
    payload = _require_object(payload)
    target = payload.get("status")

    if not target:
        raise ValidationError(
            "The request body failed validation.",
            errors=[{"field": "status", "message": "required"}],
        )
    if target not in STATUSES:
        raise ValidationError(
            "The request body failed validation.",
            errors=[
                {
                    "field": "status",
                    "message": "must be one of: {}".format(", ".join(STATUSES)),
                }
            ],
        )
    if target not in ALLOWED_TRANSITIONS.get(current_status, ()):
        raise ValidationError(
            "Cannot move a message from '{}' to '{}'.".format(
                current_status, target
            ),
            code="invalid_transition",
            status_code=409,
        )
    return target
