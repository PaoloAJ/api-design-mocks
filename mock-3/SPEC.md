# Design spec — Content Moderation API

The rules this service is built to. Any change to the codebase is measured
against them.

## Shape

- Resources are plural nouns (`/v1/cases`). A state change with side effects
  gets its own action sub-resource (`POST /v1/cases/{id}/decision`), never a
  field write on `PATCH`.
- Every response, including failures, uses the shared error envelope.
- Storage shape and wire shape are separate; handlers return `serialize(...)`,
  never a raw record.
- Ingest is not CRUD. `POST /v1/submissions` accepts a batch, returns `202`,
  and reports per-item results.

## Constraints

These are binding. **A change that violates one is wrong even if its tests
pass.**

1. Handlers **MUST NOT** load a collection to answer a request about a single
   record. Use `store.get()` or `store.find_one()`; `store.list()` is for
   collection endpoints only.

2. Filtering **MUST** happen before pagination. Never filter a page after it
   has been sliced — it produces short pages and a wrong `has_more`.

3. Tenant scoping **MUST** derive from the authenticated identity in
   `g.platform_id` and nothing else. Never from a request header, query
   parameter, or body field.

4. A resource owned by another tenant **MUST** return `404`, never `403`.
   `403` confirms the ID exists. `403` is reserved for a caller we have
   authenticated who lacks the *role* for an action.

5. `state` **MUST** be writable only through an action sub-resource. It is
   never accepted in a `PATCH` body, and never inferred from a query
   parameter.

6. Every decision **MUST** record the deciding reviewer. A moderation outcome
   with no attributed author cannot be audited and **MUST NOT** be persisted.

7. Cursors are opaque to clients and **MUST NOT** be parsed by them. A cursor
   **MUST** be rejected with `400` if it is replayed against a different
   ordering than the one that issued it.

8. Sort keys **MUST** be immutable for the life of a paging walk. Paging by a
   field that changes under the client can skip or repeat rows.

## Non-goals

- Persistence. Storage is in-memory by design; the subject is the HTTP
  contract.
- Authentication beyond static demo keys.
- Classification and reviewer notification. Both are deliberately outside this
  service: ingest records content and stops, and nothing here tells a human
  that a case is waiting.
