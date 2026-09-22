"""Opaque cursor pagination.

Offset pagination (`?page=3`) drifts: messages are queued constantly, so a
client walking the list sees duplicates or misses rows as new ones land at the
top. A cursor encodes "where I stopped" as a sort-key value, so the next page
is an index seek and concurrent inserts cannot shift the window.

The cursor is base64 only to signal "opaque, do not parse". Base64 is not
encryption — a real implementation signs it (HMAC) so a client cannot forge
one into rows it should not see.
"""

from __future__ import annotations

import base64
import json

from app.errors import ValidationError

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def encode_cursor(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor):
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if not isinstance(payload, dict):
            raise ValueError("cursor must decode to an object")
        return payload
    except Exception:
        raise ValidationError(
            "Invalid cursor. Pass the `next_cursor` from a previous page.",
            errors=[{"field": "cursor", "message": "malformed"}],
        )


def parse_limit(raw):
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


def page_response(records, serialize, *, limit, cursor):
    """Paginate `records` and build the list envelope every collection returns.

    One helper so the three list endpoints cannot drift into three slightly
    different response shapes — and so filtering stays visibly *above* the
    call, where it belongs.
    """
    page, next_cursor = paginate(records, limit=limit, cursor=cursor)
    return {
        "data": [serialize(r) for r in page],
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
    }


def paginate(records, *, limit, cursor):
    """Slice a sorted list into one page plus the cursor for the next.

    `records` must already be sorted by `(created_at, id)` descending. We look
    at one extra item to learn whether another page exists without a second
    COUNT query.
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
            # The anchor row is gone. Descending sort means the next page is
            # everything strictly older than the anchor key.
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
        next_cursor = encode_cursor(
            {"created_at": last["created_at"], "id": last["id"]}
        )
    return page, next_cursor
