# mock-2 — Incidents API

A small Flask REST API used as a practice surface for a Datadog SWE-intern
technical interview. The domain is incident response: an **incident**
belongs to a **service**, moves through a **status** lifecycle, collects
**responders**, and accumulates an append-only **timeline**.

There is no database. Storage is an in-memory dict behind a lock, because the
subject here is the HTTP contract, not persistence — same rationale as
[`mock-1`](../mock-1).

## How this relates to mock-1

Per the official prep guide (`SWE Intern Interview Prep - NorAm.pdf`), the
Datadog technical interview has **three phases**: explore an unfamiliar
codebase, design a new feature verbally, and review a PR. `mock-1` drills the
first two in depth for one domain (monitors/alerts). This repo is a *second*,
unfamiliar domain to practice cold-exploration on, and it adds the phase
`mock-1` doesn't cover:

| Phase | Where |
|---|---|
| Explore an unfamiliar codebase | This repo — `app/`, start from this README |
| Design a feature verbally | [`INTERVIEWER.md`](INTERVIEWER.md) §5 |
| Review a PR | [`pr_review/`](pr_review/) |

Read [`INTERVIEWER.md`](INTERVIEWER.md) for the dataflow trace, the question
bank, and how to run a timed session against yourself or a study partner.

## Run it

```bash
cd mock-2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python wsgi.py          # http://127.0.0.1:5002
pytest -q               # 55 tests
```

> Port **5002** — `mock-1` already claims 5001, and 5000 is macOS AirPlay
> Receiver, which replies with an empty `403` that looks exactly like an auth
> bug in your own app.

Every request except `/` and `/health` needs an API key:

```bash
curl -H "DD-API-KEY: dd-demo-key-alpha" http://127.0.0.1:5002/v1/incidents
```

Two demo keys map to two tenants: `dd-demo-key-alpha` → `org_alpha`,
`dd-demo-key-beta` → `org_beta`. They cannot see each other's data. (Same
keys as `mock-1`, on purpose — one thing to remember, not two.)

## Endpoints

| Method | Path | Purpose | Success |
|---|---|---|---|
| `GET` | `/health` | Liveness probe, no auth | 200 |
| `GET` | `/` | Discovery document | 200 |
| `GET` | `/v1/services` | List the service catalog | 200 |
| `GET` | `/v1/services/{id}` | Fetch one service | 200 |
| `GET` | `/v1/incidents` | List; filter + cursor paginate | 200 |
| `POST` | `/v1/incidents` | Create (idempotent via header) | 201 |
| `GET` | `/v1/incidents/{id}` | Fetch one; supports `If-None-Match` | 200 / 304 |
| `PATCH` | `/v1/incidents/{id}` | Partial update; guarded by `If-Match` | 200 |
| `POST` | `/v1/incidents/{id}/status` | Transition state (strict FSM) | 200 |
| `POST` | `/v1/incidents/{id}/responders` | Add a responder (naturally idempotent) | 200 |
| `DELETE` | `/v1/incidents/{id}/responders/{handle}` | Remove a responder | 204 |
| `GET` | `/v1/incidents/{id}/timeline` | List timeline entries, paginated | 200 |
| `POST` | `/v1/incidents/{id}/timeline` | Append a note (audit log, append-only) | 201 |

Notice what's **not** here: no `DELETE /v1/incidents/{id}`, no `POST
/v1/services`, and no way to change `severity` at all. Each is a deliberate
gap — the first two are design-judgment discussion points in
`INTERVIEWER.md`; the third is exactly what the PR in `pr_review/` adds, with
bugs planted in how it does it.

### Query parameters on `GET /v1/incidents`

`status`, `severity`, `service_id`, `q` (substring over title and summary),
and `tag` (repeatable — all supplied tags must match). Paging is `limit`
(default 25, max 100, clamped rather than rejected) plus `cursor`.

```bash
curl -H "DD-API-KEY: dd-demo-key-alpha" \
  "http://127.0.0.1:5002/v1/incidents?status=investigating&severity=sev1"
```

## Conventions

Same conventions as `mock-1`, applied to a different resource shape:

**One error envelope, always.**

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "errors": [{ "field": "severity", "message": "must be one of: sev1, sev2, sev3, sev4" }]
  }
}
```

**Concurrency.** Every incident carries a `version`, surfaced as a weak ETag
(`W/"inc_abc-3"`). Send it back as `If-Match` on `PATCH` to get `409` instead
of silently overwriting a concurrent edit.

**Two flavors of idempotency, on purpose.** `POST /v1/incidents` needs an
explicit `Idempotency-Key` header, the same mechanism as `mock-1`. `POST
.../responders` needs no header at all — adding a responder who's already on
the incident is a no-op by construction, because the target state fully
describes the operation. Knowing which situation you're in is worth being
able to explain out loud; see `INTERVIEWER.md`'s dataflow section for the
follow-up.

**A strict state machine, unlike mock-1.** Monitor status in `mock-1` can
move from any state to any other. Incident status here is a directed graph
(`ALLOWED_TRANSITIONS` in `app/api/incidents.py`); an illegal move is a `409`
with `invalid_state_transition`. Both designs are defensible — the
interesting interview answer is *why this domain chose which*.

**Tenant isolation.** A resource owned by another org returns `404`, not
`403` — `403` would confirm the ID exists.

**Every response carries** `X-Request-ID` (echoed from the request if you
sent one), rate-limit headers, and `X-Response-Time-Ms`.

## Layout

```
app/
  __init__.py        app factory, /health, /
  seed.py             demo data
  api/
    services.py       read-only catalog
    incidents.py       CRUD-minus-D, filtering, status transitions, responders, timeline
  core/
    errors.py          error types, single JSON envelope
    store.py            thread-safe dict store, versions, idempotency, rate limit
    pagination.py        opaque cursors
    validation.py         request validation
    middleware.py           auth, rate limiting, request IDs, timing
tests/                55 tests
pr_review/            phase-3 material: a PR to review, planted with bugs
```

`app/core/*` is close to a byte-for-byte copy of `mock-1`'s — cross-cutting
concerns (auth, rate limiting, pagination, the error envelope) are exactly
the kind of thing that shouldn't change per-domain, and recognizing that
*shouldn't* is itself a decent interview answer.

## Known limits

Deliberate, and each one is a discussion point in `INTERVIEWER.md`:

- In-memory state — resets on restart, wrong across multiple workers.
- Static API keys in source; real auth uses signed tokens.
- Rate limiting is a fixed window and is per-process.
- Cursors are base64, not signed — a client could forge one.
- Filtering and search are linear scans.
- No audit *of who changed what* beyond the timeline's `author` field, which
  is client-supplied and not verified against the API key.
