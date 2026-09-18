# API concepts to recognize

A study reference for reading HTTP API codebases cold. Each section is the
same three layers: **the concept**, **where it lives in `mock-4`**, and **what
an interviewer probes**.

Use it two ways. Reading a new repo: skim the left column of the checklist at
the bottom and go find each thing. Preparing for a round on `mock-4`: read
straight through with the files open.

---

## 0. Orientation: how to read an API repo with no README

The fastest path through an unfamiliar HTTP service, in order:

1. **Entrypoint** — find `create_app` / `main` / `index.ts`. It tells you the
   framework, what middleware is registered, and what routes exist.
   `mock-4/app.py`.
2. **Route table** — the blueprints/routers. Now you know the surface area.
   `mock-4/api/shipments.py`, `mock-4/api/scans.py`.
3. **Middleware** — what runs before every handler. This is where auth,
   identity, and rate limiting hide. `mock-4/core/middleware.py`.
4. **The domain module** — where the business rules live, separate from HTTP.
   `mock-4/services/shipping.py`.
5. **Storage** — the data access layer and its pagination. `mock-4/core/store.py`.
6. **Errors** — the shared failure shape. `mock-4/core/errors.py`.

Two habits that separate strong readers from weak ones:

- **Read the dict against the branch that consumes it.** A lookup table and the
  `if` that uses it are often in different places, and the gap between them is
  where bugs live. (In `mock-4`: `SCAN_STATES["attempted"] = None` and the
  `if target:` guard three functions below it.)
- **Say "I'd have to read X to be sure."** Guessing what a file does from its
  name is the single most visible weak signal.

---

## 1. Pagination

### Offset / page

`?page=3&limit=25` — "skip 50, give me 25."

- **Good:** simple, jump to any page, easy total counts.
- **Bad:** *drifts.* A row inserted above your position shifts everything down,
  so you see a record twice; a delete makes you skip one. Also slow at depth —
  the database walks the rows it's discarding.

### Cursor / keyset

"Give me 25 records **after this specific record**."

- **Good:** stable under concurrent writes, and fast at any depth — it becomes
  `WHERE (key, id) < (?, ?) ORDER BY ... LIMIT 25`, an index seek rather than a
  scan-and-discard.
- **Bad:** no page numbers, no jumping to page 7, usually no total count.

> **In mock-4:** `core/store.py` — `encode_cursor`, `decode_cursor`, `paginate`.
> The module docstring states the tradeoff explicitly: *"`?page=3` drifts,
> because rows created while a client walks the list make it repeat or skip
> records."*

### Opaque cursors

The client gets a string, passes it back, and **never parses it**. In `mock-4`
it's base64 of plain JSON — `{"created_at": "...", "id": "shp_3f9a"}` — so it's
trivially decodable. That's fine: base64 here is a *social signal*, not
security. The store's docstring says so directly: it *"only signals 'opaque, do
not parse' — it is not encryption, and a real service signs the cursor so it
cannot be forged into another merchant's rows."*

Why it matters: if clients don't depend on the format, the server can change
what's inside without breaking them.

**Production difference:** sign or encrypt the cursor. An unsigned cursor that
encodes a tenant boundary can be edited into someone else's rows.

### Total ordering and the tiebreaker

A sort key must be **total** — no ties — or paging skips rows. Two shipments
created in the same millisecond are indistinguishable to a `created_at` cursor,
so `mock-4` sorts by `(created_at, id)`: the ID breaks the tie and makes the
order total.

Any sort field needs this. Sorting by weight needs `(weight_kg, id)`, not
`weight_kg` alone.

### The read-one-extra trick

`paginate` fetches `limit + 1` rows. If it gets more than `limit` back, another
page exists → `has_more: true`. No `COUNT(*)` required. Recognize this pattern
on sight; it's near-universal in cursor implementations.

### Sort keys must be immutable

If you page by a field the client can edit, a row can move in the ordering
mid-walk and get skipped or repeated. `created_at` is safe. `weight_kg` is not,
unless it's frozen after creation.

> `mock-4/SPEC.md` rule 8 states this as a binding constraint.

### Filter before paginate

Slice first and you filter *within the page* — you get short pages and a wrong
`has_more`. `list_shipments` in `api/shipments.py` does filtering (tenant,
state, carrier) on the full list, then calls `paginate` last.

> `mock-4/SPEC.md` rule 2.

### 🔑 The coupling worth memorizing

**A cursor is a bookmark that is only meaningful in one ordering.** Change the
sort and every outstanding cursor is garbage — and it fails *silently*, not
loudly: `paginate` walks a weight-sorted list looking for a `(created_at, id)`
anchor, fails to match, and returns an arbitrary or empty page. The client
can't tell.

Fix: put the sort inside the cursor payload, compare on decode, reject
mismatches with `400`. `mock-4/SPEC.md` rule 7 already mandates exactly this.

---

## 2. Identity, authn, authz

**Authentication** = who are you. **Authorization** = may you do this. Different
questions, different status codes.

| Situation | Code | Why |
|---|---|---|
| No credential / bad credential | **401** | We don't know who you are |
| Known caller, wrong role | **403** | We know you; you may not do this |
| Known caller, someone else's resource | **404** | A 403 would confirm the ID exists |

That last row is the subtle one. Returning `403` for another tenant's record
leaks existence — enough to enumerate IDs and estimate a competitor's volume.
Return `404` and the two cases are indistinguishable.

> **In mock-4:** `core/middleware.py` `authenticate()` sets `g.merchant_id` and
> `g.role`. `api/shipments.py` `load_owned()` does the 404-not-403 tenant check.
> `services/shipping.py` `assert_can_file_claim()` is the 403 case — a carrier
> key belongs to the merchant but may not speak for them.
> `SPEC.md` rules 3 and 4.

### Middleware vs. per-route decorators

`mock-4` uses `before_request` hooks, and says why in the docstring: *"a newly
added endpoint is protected by default. Forgetting a decorator on one route is
how auth bypasses ship."* Secure-by-default beats secure-by-remembering.

### Order matters

The chain is `assign_request_id → authenticate → enforce_rate_limit`. Rate
limiting runs **after** auth because the counter keys on `g.api_key`. Key it on
something spoofable (an IP, an unverified header) and one caller can drain
another's budget.

### Tenant scoping comes from the token, never the request

This is the highest-severity class of API bug. If scoping reads a header,
query param, or body field, any caller can set it. "A trusted proxy sets this
header" is not a defense — the app cannot distinguish a proxy-set header from a
client-set one, and it only takes one path that reaches the app directly.

> `SPEC.md` rule 3. This is also PR defect D1 in the mock-4 exercise.

---

## 3. Resource design

- **Plural nouns for collections:** `/v1/shipments`, not `/v1/getShipment`.
- **Action sub-resources for state changes with side effects:**
  `POST /v1/shipments/{id}/transition`, not `PATCH {"state": "delivered"}`.
  The justification is the side effects — a transition stamps `delivered_at`,
  records `cancelled_by`, and changes what the shipment is eligible for. A
  field write implies none of that happened.
- **State is never writable through a normal body.** `validate_shipment` and
  `update_shipment` both explicitly reject a `state` key.

> `SPEC.md` rule 5.

### Wire shape ≠ storage shape

`serialize()` in `api/shipments.py` rebuilds a dict from a pinned field list.
It looks redundant. It isn't — it's the seam that keeps `merchant_id` off the
wire, and it means adding an internal column can't leak by accident.

**Recognize this pattern and don't "clean it up."** In the mock-4 exercise,
wanting to delete `serialize()` is an explicit weak signal.

### Don't load a collection to answer a single-record question

Detail endpoints use a keyed lookup (`store.get`, `find_one`). Calling
`store.list()` inside a detail handler sorts the entire collection to produce
one record.

> `SPEC.md` rule 1. This is PR defect D2.

---

## 4. Error contracts

One envelope for every failure, including the framework's own:

```json
{"error": {"code": "validation_error", "message": "...", "errors": [...]}}
```

- **Status code = category** (retry? fix the request? give up?).
  **`code` string = which specific thing** went wrong. Clients branch on the
  string; the status tells them how to react.
- **Catch the framework's errors too.** `core/errors.py` registers a handler
  for `HTTPException` so Flask's own 404/405/415 never come back as HTML. A
  client that must parse two failure formats will parse one of them wrong.
- **Collect all field errors, not just the first.** `validate_shipment` builds
  an `errors` list. One field per round trip is a bad API.
- **400 vs 409:** malformed request → `400`. Well-formed request that conflicts
  with current state (illegal transition, version mismatch) → `409`.

---

## 5. State machines

An explicit transition table (`TRANSITIONS` in `services/shipping.py`) rather
than scattered `if` statements. Things to recognize:

- **Terminal states** — `delivered` and `cancelled` map to an empty set.
- **Recoverable states** — `exception` can go back to `in_transit`.
- **One chokepoint.** Both the merchant path (`transition_shipment`) and the
  carrier path (`_apply_one`) call `apply_transition`. That's the whole point:
  a carrier batch cannot make a move a merchant couldn't.

> **The rule to internalize:** *every* state change goes through the checker.
> A state written directly to storage bypasses the legality check, and an
> illegal state that reaches the database can't be un-written by validating
> later. `SPEC.md` rule 6 — and PR defect D3 is exactly this violation.

When you see a lookup table, **check every value against the code that consumes
it**. `SCAN_STATES["attempted"] = None` falls through the `if target:` guard, so
a failed delivery attempt records a scan, changes nothing, and reports the old
state in a `202` as if all is well.

---

## 6. Async boundaries and batch ingest

**`202 Accepted` ≠ `201 Created`.** `201` means it's done and here's the
resource. `202` means *we've taken responsibility for this; downstream work may
not have finished.* Treating a 202 as a 201 is a named weak signal.

**Partial success is the norm in batch ingest.** `POST /v1/scans` takes up to
50 scans, applies each independently, and returns both `accepted` and `errors`.
One unknown tracking number must not force a carrier to replay 49 good scans.

**Errors carry the item `index`, not just an ID** — so the caller can retry
exactly the failed rows.

**The batch is not atomic.** Rows 1–11 are committed even if row 12 fails.
Asking "what happens on partial failure?" unprompted is a strong signal.

**Ask what's missing at the boundary.** In `mock-4`, nothing notifies the
merchant when a shipment hits `exception`, and nothing reconciles a shipment
that stops receiving scans. Both are deliberate gaps, documented in `SPEC.md`
non-goals. Narrating a notification system that doesn't exist is the flagship
weak signal for this repo.

---

## 7. Idempotency

A client whose `POST` times out doesn't know whether it succeeded. Without
idempotency, retrying books a second label.

`Idempotency-Key` header → server remembers key → resource ID, and a retry
returns the original instead of creating a new one.

`create_shipment` implements this and **documents its own gaps**, which is the
part worth recognizing: no TTL on the key, no fingerprint of the body (so the
same key with a *different* body returns the first resource), no in-flight
handling (two concurrent requests with one key both proceed), and the key isn't
scoped to the merchant (so keys collide across tenants).

Recognizing that a comment like that is a *map of the follow-up questions* is
worth more than the feature itself.

---

## 8. Concurrency

**Optimistic concurrency / ETags.** Every record carries a `version`. A read
returns `ETag: "3"`; a write sends `If-Match: "3"`; if the stored version moved,
the write gets `409` and the client re-reads. Without it, two support agents
editing the same record silently overwrite each other — lost update.

- `store.update(..., expected_version=...)` raises `ConflictError` on mismatch.
- `with_etag()` attaches the header.
- Note the asymmetry: `update_shipment` only enforces it *if* `If-Match` is
  sent. That's a deliberate looseness worth asking about.

**Locking.** `Store` wraps mutations in an `RLock` because the dict is shared
across threads. Note that `list()` copies under the lock and sorts *outside* it
— holding a lock across a sort would serialize every list call.

---

## 9. Rate limiting

**Fixed window** (what `mock-4` uses): count requests per key per 60s, reset at
the boundary. Cheap, one counter. Flaw, stated in the docstring: allows a burst
of up to **2× the limit** across a boundary — 100 at 0:59 and 100 at 1:01.

Alternatives to be able to name: **sliding window** (accurate, more state),
**token bucket** (smooth, allows controlled bursts), **leaky bucket**.

**Process-local state doesn't survive horizontal scaling.** The counter lives in
a dict in one process, so with four workers the effective limit is 4× the
configured one. Real deployments push this to Redis.

**Communicate the limit:** `X-RateLimit-Limit` / `-Remaining` / `-Reset`
headers, and `Retry-After` on the `429` (RFC 6585).

---

## 10. Observability

**Request IDs.** `assign_request_id` honors an inbound `X-Request-ID` or mints
one, and `after_request` echoes it on *every* response, errors included. That's
what lets a customer paste an ID into a ticket and have you find the log line.
Honoring an inbound one is what makes it work across service hops.

**Health vs. readiness.** Liveness (am I alive?) must be dependency-free — a
health check that queries the database fails during a blip and gets the
container killed by the orchestrator. Readiness (can I serve traffic?) is a
*different* endpoint. `mock-4` has only the one, and says so.

**Prefixed opaque IDs.** `shp_3f9a2b...` is self-describing in logs and stops a
claim ID being accepted where a shipment ID belongs. Don't parse them.

---

## 11. Smells to flag on sight

| Smell | Why it matters |
|---|---|
| Tenant scoping from a header/param/body | Any caller can set it → cross-tenant read |
| `403` for another tenant's resource | Confirms the ID exists; enumerable |
| Collection load inside a detail handler | Sorts everything to produce one record |
| Filtering after slicing the page | Short pages, wrong `has_more` |
| State written directly to storage | Bypasses the legality check, unfixable after the fact |
| Sorting by a mutable field while paging | Rows move mid-walk; skipped or repeated |
| A lookup table with a `None`/empty value | Check what the consuming branch does with it |
| A changed test in a diff | **A changed test is a changed contract** — read it first |
| Naive `datetime.now()` | No timezone; `utcnow()` here forces an explicit `Z` |
| Auth as a per-route decorator | The route someone forgets is the vulnerability |

### Not smells — don't flag these

- **A serializer that "just rebuilds the dict."** It's the seam keeping internal
  fields off the wire.
- **Base64 that isn't encryption.** It's an opacity signal, and the code says so.
- **Sorting by `created_at` in a single-record lookup.** It's not a paging walk;
  the full scan is the problem, not the sort key.

---

## 12. Recognition checklist

Run this against any HTTP API repo:

- [ ] **Entrypoint** — what middleware is registered, in what order?
- [ ] **Auth** — where does identity come from, and what's it stored in?
- [ ] **Tenant scoping** — token-derived, or attacker-controlled?
- [ ] **404 vs 403** — which does a cross-tenant read return?
- [ ] **Pagination** — offset or cursor? What's in the cursor? Is it signed?
- [ ] **Sort key** — total (has a tiebreaker)? Immutable?
- [ ] **Filter/paginate order** — which happens first?
- [ ] **Error envelope** — one shape? Does it catch framework errors?
- [ ] **State changes** — is there one chokepoint, and does everything use it?
- [ ] **Lookup tables** — read each value against the branch consuming it
- [ ] **Write endpoints** — idempotent? Optimistic concurrency?
- [ ] **Status codes** — any `202`? What does it actually promise?
- [ ] **Batch endpoints** — atomic? Per-item errors? Indexed?
- [ ] **Rate limiting** — keyed on what? Survives multiple workers?
- [ ] **The gaps** — what's deliberately *not* here? (Check the non-goals.)

---

## 13. Quick file map (mock-4)

| File | Concepts |
|---|---|
| `app.py` | Factory pattern, body size cap, health vs. readiness |
| `core/middleware.py` | Hook ordering, authn, identity in `g`, fixed-window limiting, request IDs |
| `core/errors.py` | Error envelope, status-vs-code, framework error capture |
| `core/store.py` | Cursor pagination, total ordering, version counter, locking, idempotency map |
| `services/shipping.py` | State machine, the single transition chokepoint, 403-vs-409, validation |
| `api/shipments.py` | Filter-then-paginate, tenant scoping, serializer seam, ETags, action sub-resource |
| `api/scans.py` | Async boundary, `202`, batch partial success, indexed errors, the `attempted` gap |
| `SPEC.md` | The eight binding constraints; cite rule numbers by number |
