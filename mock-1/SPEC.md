# Design spec — Monitors & Alerts API

The rules this service is built to. Any change to the codebase is measured
against them.

## Shape

- Resources are plural nouns (`/v1/monitors`). A state change with side
  effects gets its own action sub-resource (`POST /v1/monitors/{id}/status`),
  never a field write on `PATCH`.
- Every response, including failures, uses the shared error envelope.
- Storage shape and wire shape are separate; handlers return `serialize(...)`,
  never a raw record.

## Constraints

These are binding. **A change that violates one is wrong even if its tests
pass.**

1. Handlers **MUST NOT** load a collection to answer a request about a single
   record. Use `store.get()` or `store.find_one()`; `store.list()` is for
   collection endpoints only.

2. Filtering **MUST** happen before pagination. Never filter a page after it
   has been sliced — it produces short pages and a wrong `has_more`.

3. Tenant scoping **MUST** derive from the authenticated identity in `g.org_id`
   and nothing else. Never from a request header, query parameter, or body
   field.

4. A resource owned by another tenant **MUST** return `404`, never `403`.
   `403` confirms the ID exists.

5. Cursors are opaque to clients and **MUST NOT** be parsed by them. A cursor
   **MUST** be rejected with `400` if it is replayed against a different
   ordering than the one that issued it.

6. Sort keys **MUST** be immutable for the life of a paging walk. Paging by a
   field that changes under the client can skip or repeat rows.

7. User-supplied values that reach a query **MUST** be checked against an
   allowlist, not interpolated.

8. Changes to the cursor payload format **MUST** stay backward compatible for
   one release, or ship behind a version marker inside the cursor.

## Non-goals

- Persistence. Storage is in-memory by design; the subject is the HTTP
  contract.
- Authentication beyond static demo keys.
- Metric aggregation and notification delivery. Both are deliberately outside
  this service.
