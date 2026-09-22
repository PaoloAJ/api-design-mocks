"""In-memory persistence.

Deliberately not a database. The subject of this service is the HTTP contract,
so storage is a dict behind a lock with the same *semantics* a real datastore
would give: optimistic concurrency via a version counter, and a stable total
ordering so cursor pagination cannot skip or repeat rows.

Process-local, so it resets on restart and does not work across workers.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from app.core.errors import ConflictError, NotFoundError


def utcnow():
    """ISO-8601 with an explicit Z — never a naive local timestamp."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def new_id(prefix):
    """Prefixed opaque IDs (`bld_3f9a...`).

    The prefix makes an ID self-describing in logs and stops a build ID from
    being accepted where a job ID belongs. Opaque on purpose: do not parse.
    """
    return "{}_{}".format(prefix, uuid.uuid4().hex[:12])


class Store:
    """Thread-safe collection store keyed by resource type."""

    def __init__(self):
        self._lock = threading.RLock()
        self._data = {"builds": {}, "jobs": {}}
        # Per-collection ID prefixes, so an ID says what it points at.
        self._prefixes = {"builds": "bld", "jobs": "job"}
        # Idempotency-Key -> (kind, resource_id). Lets a retried POST return
        # the original resource instead of creating a duplicate build.
        self._idempotency = {}

    def create(self, kind, record):
        with self._lock:
            record = dict(record)
            record.setdefault("id", new_id(self._prefixes[kind]))
            now = utcnow()
            record["created_at"] = now
            record["updated_at"] = now
            # Version backs the ETag / If-Match check.
            record["version"] = 1
            self._data[kind][record["id"]] = record
            return dict(record)

    def get(self, kind, resource_id):
        with self._lock:
            record = self._data[kind].get(resource_id)
            if record is None:
                raise NotFoundError(
                    "{} '{}' was not found.".format(
                        kind[:-1].capitalize(), resource_id
                    )
                )
            return dict(record)

    def update(self, kind, resource_id, changes, *, expected_version=None):
        with self._lock:
            record = self._data[kind].get(resource_id)
            if record is None:
                raise NotFoundError(
                    "{} '{}' was not found.".format(
                        kind[:-1].capitalize(), resource_id
                    )
                )
            if expected_version is not None and record["version"] != expected_version:
                raise ConflictError(
                    "Build was modified by another request; re-read and retry.",
                    code="version_conflict",
                )
            record.update(changes)
            record["updated_at"] = utcnow()
            record["version"] += 1
            return dict(record)

    def list(self, kind):
        """All records, newest first, with `id` as the tiebreaker.

        The sort key must be total and stable, or cursor pagination can skip
        or repeat rows when two records share a timestamp.
        """
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

    def reset(self):
        """Wipe all state. Used by tests."""
        with self._lock:
            for bucket in self._data.values():
                bucket.clear()
            self._idempotency.clear()


store = Store()
