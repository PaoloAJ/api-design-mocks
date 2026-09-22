# Design spec — Builds API

The rules this service is built to. Any change is measured against them.

## Shape

- Resources are plural nouns (`/v1/projects/{slug}/builds`). A state change
  with side effects gets an action sub-resource
  (`POST /v1/projects/{slug}/builds/{id}/transition`), never a field write on
  `PATCH`.
- Every response, failures included, uses the shared error envelope.
- Storage shape and wire shape are separate: handlers return `serialize(...)`,
  never a raw record.
- Ingest is not CRUD. `POST /v1/events` takes a batch, returns `202`, and
  reports a per-item result.
- There is no authentication. This service sits behind an internal gateway
  that terminates auth and hands us a project slug in the path.

## Constraints

These are binding. **A change that violates one is wrong even if its tests
pass.**

1. Handlers **MUST NOT** load a collection to answer a request about a single
   record. Use `store.get()` or `store.find_one()`; `store.list()` is for
   collection endpoints only.

2. Filtering **MUST** happen before pagination. Never filter a page after it
   has been sliced — it produces short pages and a wrong `has_more`.

3. Project scoping **MUST** derive from `g.project`, which `resolve_project`
   sets from the URL path. Never from a request header, query parameter, or
   body field.

4. A record belonging to another project **MUST** return `404`, never `403`.
   `403` confirms the ID exists.

5. `state` **MUST** only be written by the transition endpoint. No other
   handler may set it, directly or through `store.update`.

6. A transition into a terminal state (`passed`, `failed`, `cancelled`)
   **MUST** leave no job in the `running` state.

7. Cursors are opaque and **MUST NOT** be parsed by clients. A cursor
   **MUST** be rejected with `400` if it is replayed against a different
   ordering than the one that issued it.

8. Sort keys **MUST** be immutable for the life of a paging walk. Paging by a
   field that changes under the client can skip or repeat rows.

## Non-goals

- Persistence. Storage is in-memory by design; the subject is the HTTP
  contract.
- Authentication and authorization. Both belong to the gateway.
- Actually running builds, and notifying anyone when one finishes. Both are
  deliberately outside this service.
