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

from app.errors import ConflictError, NotFoundError


def utcnow():
    """ISO-8601 with an explicit Z — never a naive local timestamp."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def parse_etag(raw):
    """Pull the version integer out of an `If-Match` value.

    Accepts both `W/"3"` and `"3"` — a client that echoes back exactly what we
    sent and one that strips the weak marker should both work.
    """
    value = raw.strip()
    if value.startswith("W/"):
        value = value[2:]
    return int(value.strip('"'))


def new_id(prefix):
    """Prefixed opaque IDs (`msg_3f9a...`).

    The prefix makes an ID self-describing in logs and stops a message ID from
    being accepted where a template ID belongs. Opaque on purpose: do not parse.
    """
    return "{}_{}".format(prefix, uuid.uuid4().hex[:12])


class Store:
    """Thread-safe collection store keyed by resource type."""

    def __init__(self):
        self._lock = threading.RLock()
        self._data = {"messages": {}, "templates": {}, "events": {}}
        # Per-collection ID prefixes, so an ID says what it points at.
        self._prefixes = {"messages": "msg", "templates": "tpl", "events": "evt"}
        # Idempotency-Key -> (kind, resource_id). Lets a retried send return
        # the original message instead of mailing the customer twice.
        #
        # Known gaps, left in on purpose: no TTL, so keys live forever; no
        # fingerprint of the body, so the same key with a different payload
        # returns the first message; no in-flight marker, so two concurrent
        # retries can both miss and both create; and the map is global rather
        # than per-account, so one account's key can collide with another's.
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

    def get(self, kind, resource_id, *, account_id=None):
        """Fetch one record, scoped to an account when one is given.

        A record owned by another account raises NotFound, not Forbidden: 403
        would confirm the ID exists, which lets a caller enumerate other
        accounts' message IDs by watching which ones change status code.
        """
        with self._lock:
            record = self._data[kind].get(resource_id)
            if record is None or (
                account_id is not None and record.get("account_id") != account_id
            ):
                raise NotFoundError(
                    "{} '{}' was not found.".format(
                        kind[:-1].capitalize(), resource_id
                    )
                )
            return dict(record)

    def update(self, kind, resource_id, changes, *, expected_version=None,
               account_id=None):
        with self._lock:
            record = self._data[kind].get(resource_id)
            if record is None or (
                account_id is not None and record.get("account_id") != account_id
            ):
                raise NotFoundError(
                    "{} '{}' was not found.".format(
                        kind[:-1].capitalize(), resource_id
                    )
                )
            if expected_version is not None and record["version"] != expected_version:
                raise ConflictError(
                    "Message was modified by another request; re-read and retry.",
                    code="version_conflict",
                )
            record.update(changes)
            record["updated_at"] = utcnow()
            record["version"] += 1
            return dict(record)

    def list(self, kind, *, account_id=None):
        """Records newest first, with `id` as the tiebreaker.

        The sort key must be total and stable, or cursor pagination can skip
        or repeat rows when two records share a timestamp.
        """
        with self._lock:
            records = [
                dict(r)
                for r in self._data[kind].values()
                if account_id is None or r.get("account_id") == account_id
            ]
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
