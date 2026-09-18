"""In-memory persistence and cursor pagination.

Deliberately not a database: the subject here is the HTTP contract, so storage
is a dict behind a lock offering the semantics a real datastore would — a
version counter for optimistic concurrency, and a stable total ordering that
cursor pagination can rely on. State is process-local and resets on restart.

Cursors, not offsets: `?page=3` drifts, because rows created while a client
walks the list make it repeat or skip records. A cursor encodes where the
client stopped, so concurrent inserts cannot shift the window. Base64 only
signals "opaque, do not parse" — it is not encryption, and a real service
signs the cursor so it cannot be forged into another merchant's rows.
"""

import base64
import json
import threading
import uuid
from datetime import datetime, timezone

from core.errors import ConflictError, NotFoundError, ValidationError

DEFAULT_LIMIT, MAX_LIMIT = 25, 100
# Trimming an "s" off "batches" gives "batche", so the mapping is explicit.
_SINGULAR = {"shipments": "Shipment", "claims": "Claim", "scans": "Scan"}


def utcnow():
    """ISO-8601 with an explicit Z — never a naive local timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix):
    """Prefixed opaque IDs (`shp_3f9a...`) are self-describing in logs and stop
    a claim ID being accepted where a shipment ID belongs. Do not parse them."""
    return "{}_{}".format(prefix, uuid.uuid4().hex[:12])


class Store:
    def __init__(self):
        self._lock = threading.RLock()
        self._data = {"shipments": {}, "claims": {}, "scans": {}}
        # Idempotency-Key -> (kind, id), so a retried POST returns the original
        # resource instead of booking a second label.
        self._idempotency = {}
        self._rate = {}  # api_key -> (window_start, count)

    def create(self, kind, record):
        with self._lock:
            record = dict(record)
            record.setdefault("id", new_id(kind[:3]))
            record["created_at"] = record["updated_at"] = utcnow()
            record["version"] = 1  # backs ETag / If-Match
            self._data[kind][record["id"]] = record
            return dict(record)

    def get(self, kind, resource_id):
        with self._lock:
            record = self._data[kind].get(resource_id)
            if record is None:
                raise NotFoundError("{} '{}' was not found.".format(_SINGULAR[kind], resource_id))
            return dict(record)

    def update(self, kind, resource_id, changes, *, expected_version=None):
        with self._lock:
            record = self._data[kind].get(resource_id)
            if record is None:
                raise NotFoundError("{} '{}' was not found.".format(_SINGULAR[kind], resource_id))
            if expected_version is not None and record["version"] != expected_version:
                raise ConflictError(
                    "Shipment was modified by another request; re-read and retry.",
                    code="version_conflict",
                )
            record.update(changes)
            record["updated_at"] = utcnow()
            record["version"] += 1
            return dict(record)

    def list(self, kind):
        """Newest first, `id` as tiebreaker. The sort key must be total and
        stable or paging skips rows whenever two share a timestamp."""
        with self._lock:
            records = [dict(r) for r in self._data[kind].values()]
        records.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        return records

    def find_one(self, kind, **predicates):
        with self._lock:
            for record in self._data[kind].values():
                if all(record.get(k) == v for k, v in predicates.items()):
                    return dict(record)
        return None

    def remember_idempotency(self, key, kind, resource_id):
        with self._lock:
            self._idempotency[key] = (kind, resource_id)

    def lookup_idempotency(self, key):
        with self._lock:
            return self._idempotency.get(key)

    def hit_rate_limit(self, api_key, *, limit, window_seconds, now):
        """Fixed window -> (allowed, remaining, reset_in). Cheap, but allows a
        burst of up to 2x the limit across a boundary; see INTERVIEWER.md."""
        with self._lock:
            window_start, count = self._rate.get(api_key, (now, 0))
            if now - window_start >= window_seconds:
                window_start, count = now, 0
            count += 1
            self._rate[api_key] = (window_start, count)
            reset_in = int(window_seconds - (now - window_start)) + 1
            return (count <= limit), max(limit - count, 0), reset_in

    def reset(self):
        """Wipe all state. Used by tests."""
        with self._lock:
            for bucket in self._data.values():
                bucket.clear()
            self._idempotency.clear()
            self._rate.clear()


store = Store()


def encode_cursor(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor):
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(payload, dict):
            raise ValueError
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
            "`limit` must be an integer.", errors=[{"field": "limit", "message": "not an integer"}]
        )
    if limit < 1:
        raise ValidationError(
            "`limit` must be at least 1.", errors=[{"field": "limit", "message": "must be >= 1"}]
        )
    # Clamp rather than reject: asking for 1000 gets 100 and a next_cursor,
    # friendlier than a 400 and still bounds our cost.
    return min(limit, MAX_LIMIT)


def paginate(records, *, limit, cursor):
    """Slice a list already sorted by `(created_at, id)` descending into one
    page plus the next cursor. We read one item past the page to learn whether
    another exists, which avoids a COUNT."""
    start = 0
    if cursor:
        payload = decode_cursor(cursor)
        after = (payload.get("created_at"), payload.get("id"))
        if after[0] is None or after[1] is None:
            raise ValidationError(
                "Invalid cursor.", errors=[{"field": "cursor", "message": "missing sort key"}]
            )
        start = len(records)
        for index, record in enumerate(records):
            key = (record["created_at"], record["id"])
            if key == after:
                start = index + 1
                break
            # Anchor row deleted: descending order means the next page is
            # everything strictly older than the anchor key.
            if key < after:
                start = index
                break

    window = records[start : start + limit + 1]
    page = window[:limit]
    next_cursor = None
    if len(window) > limit and page:
        next_cursor = encode_cursor({"created_at": page[-1]["created_at"], "id": page[-1]["id"]})
    return page, next_cursor
