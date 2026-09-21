"""Request-body validation.

Hand-rolled instead of pydantic/marshmallow so the repo installs with just
Flask and every rule is visible. A production service should use a schema
library — see `INTERVIEWER.md`.

The guiding rule: collect *all* field errors and return them together. An API
that fails on the first bad field forces the client into a fix-one-retry loop.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.errors import ValidationError

CURRENCIES = {"USD", "EUR", "GBP"}
PAYMENT_STATUSES = {"authorized", "captured", "voided", "settled", "failed", "disputed"}
MAX_AMOUNT = 100_000_000  # $1,000,000.00 in minor units — a sanity cap, not a real limit.
MAX_METADATA_KEYS = 20
MAX_METADATA_VALUE_LENGTH = 500


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


def _validate_metadata(metadata: Any, errors: FieldErrors) -> Optional[Dict[str, str]]:
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        errors.add("metadata", "must be an object of string to string")
        return None
    if len(metadata) > MAX_METADATA_KEYS:
        errors.add("metadata", "at most {} keys allowed".format(MAX_METADATA_KEYS))
        return None
    cleaned: Dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not key.strip():
            errors.add("metadata", "keys must be non-empty strings")
            return None
        if not isinstance(value, str):
            errors.add("metadata", "values must be strings")
            return None
        if len(value) > MAX_METADATA_VALUE_LENGTH:
            errors.add(
                "metadata", "value exceeds {} characters".format(MAX_METADATA_VALUE_LENGTH)
            )
            return None
        cleaned[key.strip()] = value
    return cleaned


def validate_payment_create(body: Any) -> Dict[str, Any]:
    body = require_json(body)
    errors = FieldErrors()

    amount = body.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int):
        errors.add("amount", "required, must be an integer number of minor units (cents)")
    elif amount <= 0:
        errors.add("amount", "must be greater than 0")
    elif amount > MAX_AMOUNT:
        errors.add("amount", "exceeds the maximum of {}".format(MAX_AMOUNT))

    currency = body.get("currency")
    if not isinstance(currency, str) or currency.upper() not in CURRENCIES:
        errors.add(
            "currency", "must be one of: {}".format(", ".join(sorted(CURRENCIES)))
        )

    customer_id = body.get("customer_id")
    if not isinstance(customer_id, str) or not customer_id.strip():
        errors.add("customer_id", "required, must be a non-empty string")
    elif len(customer_id) > 200:
        errors.add("customer_id", "must be at most 200 characters")

    description = body.get("description")
    if description is not None:
        if not isinstance(description, str):
            errors.add("description", "must be a string")
        elif len(description) > 500:
            errors.add("description", "must be at most 500 characters")

    metadata = _validate_metadata(body.get("metadata"), errors)

    # Unknown fields are rejected rather than ignored: a client that sends
    # `amonut` should hear about the typo instead of silently losing the value.
    allowed = {"amount", "currency", "customer_id", "description", "metadata"}
    for key in body:
        if key not in allowed:
            errors.add(key, "unknown field")

    errors.raise_if_any()

    return {
        "amount": amount,
        "currency": currency.upper(),
        "customer_id": customer_id.strip(),
        "description": (description or "").strip(),
        "metadata": metadata or {},
        "status": "authorized",
        "amount_refunded": 0,
        "captured_at": None,
        "voided_at": None,
        "settled_at": None,
        "settlement_reference": None,
    }


def validate_payment_patch(body: Any, current: Dict[str, Any]) -> Dict[str, Any]:
    """PATCH: only the supplied fields change.

    `amount` and `currency` are absent from the allowed set on purpose — they
    describe money that has already moved (or been held); changing them after
    the fact would rewrite history instead of recording a new event.
    """
    body = require_json(body)
    errors = FieldErrors()
    changes: Dict[str, Any] = {}

    allowed = {"description", "metadata"}
    for key in body:
        if key not in allowed:
            errors.add(key, "unknown or immutable field")

    if "description" in body:
        description = body["description"]
        if not isinstance(description, str):
            errors.add("description", "must be a string")
        elif len(description) > 500:
            errors.add("description", "must be at most 500 characters")
        else:
            changes["description"] = description.strip()

    if "metadata" in body:
        metadata = _validate_metadata(body["metadata"], errors)
        if metadata is not None:
            changes["metadata"] = metadata

    errors.raise_if_any()
    if not changes:
        raise ValidationError("Request body must contain at least one field to update.")
    return changes


def validate_status_transition(body: Any) -> str:
    body = require_json(body)
    status = body.get("status")
    if status not in PAYMENT_STATUSES:
        raise ValidationError(
            "Invalid status.",
            errors=[
                {
                    "field": "status",
                    "message": "must be one of: {}".format(
                        ", ".join(sorted(PAYMENT_STATUSES))
                    ),
                }
            ],
        )
    return status


def validate_refund_request(body: Any, payment: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a refund request against the payment's remaining balance.

    Spec rule: a refund amount is checked against what is still refundable
    (`amount - amount_refunded`), never against the original `amount` — the
    original amount ignores refunds already issued.
    """
    body = require_json(body) if body else {}
    errors = FieldErrors()

    remaining = payment["amount"] - payment["amount_refunded"]

    amount = body.get("amount", remaining)
    if isinstance(amount, bool) or not isinstance(amount, int):
        errors.add("amount", "must be an integer number of minor units (cents)")
    elif amount <= 0:
        errors.add("amount", "must be greater than 0")
    elif amount > remaining:
        errors.add(
            "amount",
            "exceeds the refundable balance of {} for this payment".format(remaining),
        )

    reason = body.get("reason")
    if reason is not None:
        if not isinstance(reason, str):
            errors.add("reason", "must be a string")
        elif len(reason) > 300:
            errors.add("reason", "must be at most 300 characters")

    allowed = {"amount", "reason"}
    for key in body:
        if key not in allowed:
            errors.add(key, "unknown field")

    errors.raise_if_any()

    return {"amount": amount, "reason": (reason or "").strip()}
