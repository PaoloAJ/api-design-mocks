# mock-1 — Monitors & Alerts API

A small Flask REST API used as a practice surface for an API-design interview.
The domain is observability: **monitors** watch a query, change **status**, and
emit **alerts**; a separate ingest endpoint accepts **metric series**.

There is no database. Storage is an in-memory dict behind a lock, because the
subject here is the HTTP contract, not persistence.

> **The interview round this targets is verbal — no code is written.** The
> reported hurdle is explaining *how data flows through the app*, out loud.
> Build and run this anyway: the point is to have traced a real request path
> yourself, so the explanation comes from memory rather than theory. See
> [`INTERVIEWER.md`](INTERVIEWER.md) §0 for the format and §1 for the flow.

## Run it

```bash
cd mock-1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python wsgi.py          # http://127.0.0.1:5001
pytest -q               # 48 tests
```

> Port **5001**, not 5000 — macOS AirPlay Receiver occupies 5000 and replies
> with an empty `403`, which looks exactly like an auth bug in your own app.

Every request except `/` and `/health` needs an API key:

```bash
curl -H "DD-API-KEY: dd-demo-key-alpha" http://127.0.0.1:5001/v1/monitors
```

Two demo keys map to two tenants: `dd-demo-key-alpha` → `org_alpha`,
`dd-demo-key-beta` → `org_beta`. They cannot see each other's data.

## Endpoints

| Method | Path | Purpose | Success |
|---|---|---|---|
| `GET` | `/health` | Liveness probe, no auth | 200 |
| `GET` | `/` | Discovery document | 200 |
| `GET` | `/v1/monitors` | List; filter + cursor paginate | 200 |
| `POST` | `/v1/monitors` | Create (idempotent via header) | 201 |
| `GET` | `/v1/monitors/{id}` | Fetch one; supports `If-None-Match` | 200 / 304 |
| `PATCH` | `/v1/monitors/{id}` | Partial update; guarded by `If-Match` | 200 |
| `DELETE` | `/v1/monitors/{id}` | Delete | 204 |
| `POST` | `/v1/monitors/{id}/status` | Transition state, open/resolve alerts | 200 |
| `GET` | `/v1/monitors/{id}/alerts` | Alerts for one monitor | 200 |
| `GET` | `/v1/alerts` | List alerts; filterable | 200 |
| `GET` | `/v1/alerts/{id}` | Fetch one alert | 200 |
| `POST` | `/v1/alerts/{id}/acknowledge` | Acknowledge an open alert | 200 |
| `POST` | `/v1/series` | Bulk metric ingest, partial success | 202 |

### Query parameters on `GET /v1/monitors`

`status`, `type`, `enabled`, `q` (substring over name and query), and `tag`
(repeatable — all supplied tags must match). Paging is `limit` (default 25,
max 100, clamped rather than rejected) plus `cursor`.

```bash
curl -H "DD-API-KEY: dd-demo-key-alpha" \
  "http://127.0.0.1:5001/v1/monitors?tag=env:prod&tag=team:api&limit=10"
```

## Conventions

**One error envelope, always.** Including Flask's own 404/405 and any
unhandled exception, so a client never has to parse HTML:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "errors": [{ "field": "type", "message": "must be one of: apm, log, metric, synthetic" }]
  }
}
```

`code` is the stable string clients branch on; the HTTP status gives the
category. Validation reports **every** bad field at once rather than failing on
the first.

**Concurrency.** Every monitor carries a `version`, surfaced as a weak ETag
(`W/"mon_abc-3"`). Send it back as `If-Match` on `PATCH` to get `409` instead
of silently overwriting a concurrent edit, or as `If-None-Match` on `GET` to
get `304` instead of a body you already have.

**Idempotency.** `POST /v1/monitors` accepts an `Idempotency-Key` header. A
replay returns the original resource with `200` and `Idempotent-Replay: true`
rather than creating a duplicate.

**Tenant isolation.** A resource owned by another org returns `404`, not `403`
— `403` would confirm the ID exists.

**Every response carries** `X-Request-ID` (echoed from the request if you sent
one), rate-limit headers, and `X-Response-Time-Ms`.

## Layout

```
app/
  __init__.py        app factory, /health, /
  seed.py            demo data
  api/
    monitors.py      CRUD, filtering, status transitions
    alerts.py        read + acknowledge
    metrics.py       bulk ingest
  core/
    errors.py        error types, single JSON envelope
    store.py         thread-safe dict store, versions, idempotency, rate limit
    pagination.py    opaque cursors
    validation.py    request validation
    middleware.py    auth, rate limiting, request IDs, timing
tests/               48 tests
```

## Known limits

Deliberate, and each one is a discussion point in `INTERVIEWER.md`:

- In-memory state — resets on restart, wrong across multiple workers.
- Static API keys in source; real auth uses signed tokens.
- Rate limiting is a fixed window (allows a 2x burst across the boundary) and
  is per-process.
- Cursors are base64, not signed — a client could forge one.
- Filtering and search are linear scans.
- No OpenAPI spec is generated; the table above is hand-maintained.
