"""Opaque cursor pagination.

Offset pagination (`?page=3`) drifts: rows inserted while a client walks the
list make it see duplicates or miss rows, and deep offsets force the database
to count past everything it skips. A cursor encodes "where I stopped" as a
sort-key value, so the next page is an index seek and concurrent inserts
cannot shift the window under the client.

The cursor is base64 here only to signal "opaque, do not parse". Base64 is not
encryption — a real implementation signs it (HMAC) so a client cannot forge a
cursor into another tenant's rows.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from app.core.errors import ValidationError

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def encode_cursor(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> Dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("cursor must decode to an object")
        return payload
    except Exception:
        raise ValidationError(
            "Invalid cursor. Pass the `next_cursor` value from a previous page.",
            errors=[{"field": "cursor", "message": "malformed"}],
        )


def parse_limit(raw: Optional[str]) -> int:
    if raw is None:
        return DEFAULT_LIMIT
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(
            "`limit` must be an integer.",
            errors=[{"field": "limit", "message": "not an integer"}],
        )
    if limit < 1:
        raise ValidationError(
            "`limit` must be at least 1.",
            errors=[{"field": "limit", "message": "must be >= 1"}],
        )
    # Clamp rather than reject: a client asking for 1000 gets 100 and a
    # next_cursor, which is friendlier than a 400 and still bounds our cost.
    return min(limit, MAX_LIMIT)


def paginate(
    records: List[Dict[str, Any]], *, limit: int, cursor: Optional[str]
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Slice a sorted list into one page plus the cursor for the next.

    `records` must already be sorted by `(created_at, id)` descending. We take
    one item beyond the page to learn whether another page exists, which
    avoids a second COUNT query.
    """
    start = 0
    if cursor:
        payload = decode_cursor(cursor)
        after_created = payload.get("created_at")
        after_id = payload.get("id")
        if after_created is None or after_id is None:
            raise ValidationError(
                "Invalid cursor.",
                errors=[{"field": "cursor", "message": "missing sort key"}],
            )
        # Skip everything up to and including the record the cursor points at.
        for index, record in enumerate(records):
            if (record["created_at"], record["id"]) == (after_created, after_id):
                start = index + 1
                break
        else:
            # The anchor row was deleted. Descending sort means "the next page"
            # is everything strictly older than the anchor key.
            start = len(records)
            for index, record in enumerate(records):
                if (record["created_at"], record["id"]) < (after_created, after_id):
                    start = index
                    break

    window = records[start : start + limit + 1]
    page = window[:limit]
    next_cursor = None
    if len(window) > limit and page:
        last = page[-1]
        next_cursor = encode_cursor({"created_at": last["created_at"], "id": last["id"]})
    return page, next_cursor
