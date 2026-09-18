"""In-memory persistence.

Deliberately not a database. The subject of this service is the HTTP contract,
so storage is a dict behind a lock that offers the same *semantics* a real
datastore would: unique constraints, optimistic concurrency via a version
counter, and a stable total ordering for pagination.

Everything here is process-local: it resets on restart and is not shared
between workers. `INTERVIEWER.md` covers what changes when this becomes
Postgres.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.errors import ConflictError, NotFoundError


def utcnow() -> str:
    """ISO-8601 with an explicit Z — never a naive local timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def new_id(prefix: str) -> str:
    """Prefixed opaque IDs (`itm_3f9a...`).

    Prefixes make IDs self-describing in logs and stop an item ID from being
    accepted where a case ID belongs. They are opaque on purpose: clients must
    not parse them.
    """
    return "{}_{}".format(prefix, uuid.uuid4().hex[:16])


# Singular labels for error messages. Deriving them by trimming an "s" off the
# collection name gets "casis" from "cases", so the mapping is explicit.
_SINGULAR = {"items": "Item", "cases": "Case", "appeals": "Appeal"}


class Store:
    """Thread-safe collection store keyed by resource type."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: Dict[str, Dict[str, Dict[str, Any]]] = {
            "items": {},
            "cases": {},
            "appeals": {},
        }
        # Idempotency-Key -> (resource_type, resource_id). Lets a retried POST
        # return the original resource instead of creating a duplicate.
        self._idempotency: Dict[str, Tuple[str, str]] = {}
        # Simple fixed-window rate limiter: api_key -> (window_start, count).
        self._rate: Dict[str, Tuple[float, int]] = {}

    # ---------------------------------------------------------------- CRUD

    def create(self, kind: str, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            record = dict(record)
            record.setdefault("id", new_id(kind[:3]))
            now = utcnow()
            record["created_at"] = now
            record["updated_at"] = now
            # Version backs ETag / If-Match optimistic concurrency.
            record["version"] = 1
            self._data[kind][record["id"]] = record
            return dict(record)

    def get(self, kind: str, resource_id: str) -> Dict[str, Any]:
        with self._lock:
            record = self._data[kind].get(resource_id)
            if record is None:
                raise NotFoundError(
                    "{} '{}' was not found.".format(_SINGULAR[kind], resource_id)
                )
            return dict(record)

    def update(
        self,
        kind: str,
        resource_id: str,
        changes: Dict[str, Any],
        *,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            record = self._data[kind].get(resource_id)
            if record is None:
                raise NotFoundError(
                    "{} '{}' was not found.".format(_SINGULAR[kind], resource_id)
                )
            if expected_version is not None and record["version"] != expected_version:
                raise ConflictError(
                    "Resource was modified by another request; re-read and retry.",
                    code="version_conflict",
                )
            record.update(changes)
            record["updated_at"] = utcnow()
            record["version"] += 1
            return dict(record)

    def delete(self, kind: str, resource_id: str) -> None:
        with self._lock:
            if resource_id not in self._data[kind]:
                raise NotFoundError(
                    "{} '{}' was not found.".format(_SINGULAR[kind], resource_id)
                )
            del self._data[kind][resource_id]

    def list(self, kind: str) -> List[Dict[str, Any]]:
        """All records, newest-first, with `id` as the tiebreaker.

        The sort key must be total and stable or cursor pagination can skip or
        repeat rows whenever two records share a timestamp.
        """
        with self._lock:
            records = [dict(r) for r in self._data[kind].values()]
        records.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        return records

    def find_one(self, kind: str, **predicates: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            for record in self._data[kind].values():
                if all(record.get(k) == v for k, v in predicates.items()):
                    return dict(record)
        return None

    # --------------------------------------------------------- idempotency

    def remember_idempotency(self, key: str, kind: str, resource_id: str) -> None:
        with self._lock:
            self._idempotency[key] = (kind, resource_id)

    def lookup_idempotency(self, key: str) -> Optional[Tuple[str, str]]:
        with self._lock:
            return self._idempotency.get(key)

    # --------------------------------------------------------- rate limits

    def hit_rate_limit(
        self, api_key: str, *, limit: int, window_seconds: int, now: float
    ) -> Tuple[bool, int, int]:
        """Fixed-window counter.

        Returns `(allowed, remaining, reset_in_seconds)`. Fixed windows are
        cheap but allow a burst of up to 2x the limit across a window
        boundary; `INTERVIEWER.md` has the sliding-window/token-bucket
        discussion.
        """
        with self._lock:
            window_start, count = self._rate.get(api_key, (now, 0))
            if now - window_start >= window_seconds:
                window_start, count = now, 0
            count += 1
            self._rate[api_key] = (window_start, count)
            reset_in = int(window_seconds - (now - window_start)) + 1
            if count > limit:
                return False, 0, reset_in
            return True, limit - count, reset_in

    # --------------------------------------------------------------- reset

    def reset(self) -> None:
        """Wipe all state. Used by tests."""
        with self._lock:
            for bucket in self._data.values():
                bucket.clear()
            self._idempotency.clear()
            self._rate.clear()


store = Store()
