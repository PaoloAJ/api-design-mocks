# Design spec — Shipping API

The rules this service is built to. Any change is measured against them.

## Shape

- Resources are plural nouns (`/v1/shipments`). A state change with side
  effects gets an action sub-resource (`POST /v1/shipments/{id}/transition`),
  never a field write on `PATCH`.
- Every response, failures included, uses the shared error envelope.
- Storage shape and wire shape are separate: handlers return `serialize(...)`,
  never a raw record.
- Ingest is not CRUD. `POST /v1/scans` accepts a batch, returns `202`, and
  reports a per-item result.

## Constraints

These are binding. **A change that violates one is wrong even if its tests
pass.**

1. Handlers **MUST NOT** load a collection to answer a request about a single
   record. Use `store.get()` or `store.find_one()`; `store.list()` is for
   collection endpoints only.

2. Filtering **MUST** happen before pagination. Never filter a page after it
   has been sliced — it produces short pages and a wrong `has_more`.

3. Tenant scoping **MUST** derive from the authenticated identity in
   `g.merchant_id` and nothing else. Never from a request header, query
   parameter, or body field.

4. A shipment owned by another merchant **MUST** return `404`, never `403`.
   `403` confirms the ID exists. `403` is reserved for a caller we have
   authenticated who lacks the *role* for an action.

5. `state` **MUST** be writable only through the transition sub-resource. It is
   never accepted in a `POST` or `PATCH` body, and never inferred from a query
   parameter.

6. Every transition **MUST** go through `apply_transition`. A state written
   directly to the store bypasses the legality check, and an illegal move that
   reaches storage cannot be undone by validation later.

7. Cursors are opaque and **MUST NOT** be parsed by clients. A cursor **MUST**
   be rejected with `400` if replayed against a different ordering than the one
   that issued it.

8. Sort keys **MUST** be immutable for the life of a paging walk. Paging by a
   field that changes under the client can skip or repeat rows.

## Non-goals

- Persistence. Storage is in-memory by design; the subject is the HTTP
  contract.
- Authentication beyond static demo keys.
- Carrier rate shopping and merchant notification. Both are deliberately
  outside this service: scan ingest records what the carrier reported and
  stops, and nothing here tells a merchant that a shipment went into
  `exception`.
