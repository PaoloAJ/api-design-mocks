# Interviewer Guide — REST API Design (Datadog)

Companion to `README.md`. That one documents the API; this one is the script
for probing whether the candidate understands *why* it is shaped this way.

The repo is a monitors/alerts/metrics API in Flask with an in-memory store.
About 1,200 lines of application code and 48 tests, small enough to read in
ten minutes and opinionated enough to argue with.

---

## 0. Format: this round is verbal, not a coding test

Based on first-hand reports from candidates who have sat this round:

> "API round def study restAPI design. Really important. I would practice by
> building a flask app in python and creating endpoints for the app.
> Understand client/server interactions and all the HTTP methods,
> idempotency, etc."
>
> Asked whether a deep or high-level understanding was needed, and whether it
> was about tracking how data comes in through a service and how you'd
> structure the API around it:
> **"Yes, I would say that. And yes exactly how you say, understanding how
> data flows through the app was the biggest hurdle."**

**What this means for how you run the session:**

- **No code is written.** The candidate talks; you probe. This repo is a
  shared reference so both of you are discussing something concrete instead of
  an imaginary whiteboard API.
- **Data flow is the main event.** The single highest-signal question is
  "trace a request end to end." Everything else is secondary.
- **Depth is expected, not just vocabulary.** "Idempotency means safe to
  retry" is a definition. "POST isn't idempotent, so a client that times out
  can't retry safely — here's the header that fixes it, and here's what breaks
  if two retries race" is understanding. Push until you hit the boundary of
  what they actually know.
- **Explaining clearly is part of the evaluation.** The same report noted that
  most of the project round was spent making something complicated
  understandable. A candidate who can only explain their design to someone who
  already knows it has not demonstrated the skill.

**The four topics named explicitly by past candidates** — make sure every one
gets covered:

| Topic | Where it lives here | Lead question |
|---|---|---|
| Client/server interaction | Request lifecycle, §1 | Q1 |
| HTTP methods | `PATCH` vs `PUT`, verbs on actions | Q2, Q3 |
| Idempotency | `Idempotency-Key`, retries, `DELETE` | Q8, Q10 |
| Data flow | All of §1 | **Q0**, Q1 |

A candidate can be strong on the others and still fail this round if they
cannot trace data through a system.

---

## 1. Dataflow summary

### Request lifecycle

Every request passes the same gauntlet before a handler runs. Order matters and
is worth asking about.

```
HTTP request
  │
  ├─ before_request: assign_request_id
  │     X-Request-ID from the client, or a fresh uuid4; start the timer
  │
  ├─ before_request: authenticate
  │     /health and / skip this
  │     DD-API-KEY header (or Bearer token) -> org_id in flask.g
  │     missing -> 401 missing_api_key    unknown -> 401 invalid_api_key
  │
  ├─ before_request: enforce_rate_limit
  │     fixed window, 100 req / 60s, keyed by API key
  │     over -> 429 + Retry-After
  │
  ├─ route handler
  │     validate body      -> 400 with every field error collected
  │     check ownership    -> 404 if another org owns it
  │     check version      -> 409 if If-Match is stale
  │     read/write store   (RLock held for the critical section)
  │     serialize          (storage shape -> wire shape)
  │
  ├─ errorhandler (if anything raised)
  │     APIError / HTTPException / bare Exception -> one JSON envelope
  │
  └─ after_request: attach_headers
        X-Request-ID, X-RateLimit-*, X-Response-Time-Ms, access log line
```

Auth precedes rate limiting so the counter is keyed by a *known* identity;
otherwise an unauthenticated flood consumes some other tenant's budget. Both
are `before_request` hooks, not per-route decorators — a new endpoint is then
protected by default, and nobody ships an auth bypass by forgetting a line.

### Monitor state machine

The only writer of `status` is `POST /v1/monitors/{id}/status`. Alerts are a
side effect, never created directly by a client.

```
   any state can move to any other; these are the meaningful edges

        ok  ──────────────>  warn  ──────────────>  alert
         ▲                     │                      │
         │                     │                      │
         └─────────────────────┴──────────────────────┘
                     back to ok (resolves alerts)

   no_data is accepted but falls through BOTH branches: it creates no
   alert and resolves none. See Q26 -- this is a planted gap.

transition INTO warn/alert   -> create alert (open, warning|critical)
transition INTO ok           -> resolve every open alert for that monitor
transition to the same state -> no-op, returns 200, creates nothing
```

That last line is the one to press on: a monitor flapping or a retried webhook
must not open ten alerts. The no-op check is in
`app/api/monitors.py:transition_status`.

### Alert lifecycle

```
open ──acknowledge──> acknowledged ──monitor returns to ok──> resolved
  │                                                              ▲
  └──────────────── monitor returns to ok ───────────────────────┘

acknowledge an already-acknowledged alert -> 200, no-op (retry-safe)
acknowledge a resolved alert              -> 409 invalid_state_transition
```

### Ingest path (`POST /v1/series`)

Deliberately unlike the CRUD resources:

```
batch of up to 1000 series
  │
  ├─ > 1000 items          -> 413 payload_too_large
  ├─ validate each item independently
  │     metric name, points as [timestamp, value],
  │     timestamp within -24h .. +10m, <= 20 tags, tags are key:value
  │
  ├─ >= 1 accepted -> 202 {accepted, rejected, errors: [{index, reason}]}
  └─ 0 accepted    -> 400 (same body shape)
```

`202`, not `201`: the write is acknowledged as durable, but aggregation is
asynchronous and the point is not queryable yet. Failures carry the **index**
so the client can map them back to what it sent.

---

## 2. Questions to ask

Grouped by depth. Each has the answer the code implements and what a strong
response adds. The candidate does not have to agree with the code — several of
these are genuinely two-sided, and defending the other side well is a better
signal than reciting this file.

Since nothing is written down, **the follow-up is where the signal is.** A
rehearsed definition and real understanding sound identical for about one
sentence. Ask "why?" or "what breaks?" twice and they stop sounding alike.

### Q0 — The data-flow question (ask this one first, always)

This is the question the round is really about. Budget 8-10 minutes; it is
worth more than any three others combined.

**"A customer's agent submits a metric. That metric crosses a threshold. An
on-call engineer gets paged and acknowledges it. Walk me through every hop —
what data moves, what shape it is in, and what the system does at each step."**

Let them talk. Interrupt only to unstick them. A complete answer traverses:

```
agent ──POST /v1/series──> ingest
                             │  auth, rate limit, per-point validation
                             │  202 Accepted (async: not yet queryable)
                             ▼
                        [aggregation]          <- not in this repo; ask anyway
                             │
                             ▼
                     evaluator compares metric to monitor.thresholds
                             │
                             ▼
        POST /v1/monitors/{id}/status  {"status": "alert"}
                             │  status changes AND an alert row is created
                             ▼
                   [notification fan-out]      <- not in this repo; ask anyway
                             │
                             ▼
          engineer ──POST /v1/alerts/{id}/acknowledge──> state: acknowledged
```

**Grading this answer:**

- *Weak* — describes endpoints in isolation, as a list, without connecting
  them. Cannot say what triggers the next step.
- *Adequate* — connects the hops in order and knows which endpoint does what.
- *Strong* — names the **asynchronous boundary** (why ingest returns `202`,
  not `201` — the point is accepted but not yet queryable), notices the two
  gaps the repo does not implement (evaluation and notification) and asks
  about them rather than pretending they exist, and identifies where state
  actually changes versus where data merely passes through.

**Follow-ups, in escalating order:**

1. "Where can this lose data, and does that matter?" — the `202` window: we
   acknowledged the point but have not aggregated it. For telemetry, some loss
   is an acceptable trade for throughput. That is a deliberate product
   decision, not an accident.
2. "The notification step fails. What happens?" — the alert row already
   exists, so state is correct but nobody was told. Leads to retries, queues,
   dead-letter handling.
3. "Two points for the same metric arrive out of order." — Does the evaluator
   use event time or arrival time? This separates people who have operated a
   pipeline from people who have only read about one.
4. "Where would you put a queue, and why there?" — Between ingest and
   aggregation. Ingest should validate and enqueue, nothing more.

### Warm-up — orienting in the system

Short questions to confirm they can navigate the repo before the harder
material. Two minutes each, not more.

**Q1. Walk me through what happens on `GET /v1/monitors?tag=env:prod`.**
Looking for: the middleware chain (request ID → auth → rate limit), then
filter → paginate → serialize. A strong answer notices filtering happens
*before* pagination (the reverse is subtly wrong — you would paginate a set
and then shrink it, giving short pages) and that `serialize()` exists so the
storage and wire shapes can diverge.

**Q2. Why `PATCH` and not `PUT` for updates?**
`PUT` replaces the whole resource, so the client must echo back server-owned
fields (`status`, `version`, `created_at`) and risks clobbering what it does
not know about. `PATCH` sends only what changed. Strong answer: `PUT` is
idempotent and `PATCH` is not inherently so, and mentions JSON Merge Patch
(RFC 7386) vs JSON Patch (RFC 6902) — and that "how do I null a field?" is the
classic merge-patch ambiguity.

**Q3. Why does `POST /v1/monitors/{id}/status` exist instead of letting
`PATCH` set `status`?**
Because the transition has side effects — it opens or resolves alerts. Hiding
that behind a field write makes `PATCH` unpredictable. Strong answer connects
this to the general rule: when an update is an *event* rather than an
assignment, give it its own endpoint.

**Q4. Why is `version` in the response body at all?**
It backs the ETag. Strong answer: clients that cannot easily set headers can
still implement optimistic concurrency, though the header is the canonical
mechanism.

### Core — design judgment

**Q5. Why cursor pagination instead of `?page=3&per_page=25`?**
Offsets drift under concurrent writes — insert a row while a client walks the
list and it sees duplicates or misses rows — and deep offsets make the
database count past everything it skips. Cursors encode the sort key, so the
next page is an index seek.
Follow up: *what breaks if the sort key is not unique?* Answer: rows sharing a
`created_at` get skipped or repeated, which is exactly why the key is
`(created_at, id)`. `tests/test_pagination.py` covers this.

**Q6. The cursor is base64 JSON. Is that a problem?**
Yes. Base64 is not encryption — anyone can decode and *forge* one, e.g. into
another tenant's range. The fix is an HMAC signature, or an opaque server-side
handle. Strong answer also notes base64 signals "do not parse this" to
well-behaved clients, which is worth something, just not security.

**Q7. `limit=100000` is clamped to 100 rather than rejected with a 400. Defend
or attack that.**
Both defensible. Clamping is friendlier and still bounds server cost; the
client sees `limit: 100` and a `next_cursor` in the response. Rejecting is
more explicit and surfaces client bugs instead of silently doing something
else. Looking for: awareness that the response *states* the effective limit,
so the behavior is at least discoverable.

**Q8. Walk me through the idempotency-key flow. What is missing?**
A repeated key returns the original resource with `200` + `Idempotent-Replay`.
Gaps a strong candidate finds:
- Keys never expire — unbounded memory. Real systems TTL them (Stripe: 24h).
- The stored key is not tied to a **request-body fingerprint**, so the same key
  with a *different* body silently returns the wrong resource instead of `422`.
- No in-flight handling: two concurrent requests with one key can both miss the
  lookup and both create. Needs a unique index or a "reserved" marker.
- Keys are global, not scoped per tenant — one org could collide with another.

**Q9. Why `404` for another tenant's monitor instead of `403`?**
`403` confirms the ID exists, which leaks information across tenants — an
attacker can enumerate IDs. Strong answer: the check lives in `_get_owned()`,
one place, and notes the tradeoff that debugging genuine permission problems
gets harder.

**Q10. `DELETE` twice gives `204` then `404`. Is that right?**
Genuinely two-sided, and the code has a comment saying so. `404` lets a client
distinguish "I deleted it" from "it was never there". Always-`204` is more
strictly idempotent and simpler for retrying clients. Looking for a *reason*,
not a preference. Follow up: *soft delete instead?* — which leads to whether a
deleted monitor's historical alerts should survive.

**Q11. Where would you version this API when you need a breaking change?**
Current scheme is a URL prefix (`/v1`). Alternatives: `Accept` header content
negotiation, or a date-based version header (Stripe's model). Strong answer
distinguishes breaking from additive — adding a field is not breaking, so most
changes need no new version — and mentions that clients rejecting unknown
fields makes additive changes breaking in practice.

**Q12. Unknown fields in a create body are rejected. Why, and when is that
wrong?**
Rejecting catches typos (`nmae`) that would otherwise silently drop data. It
is wrong when clients round-trip a `GET` response back into a `PUT`/`POST` and
a newer server version added a field their older client does not know — strict
rejection then breaks forward compatibility.

### Depth — scale and operations

**Q13. Replace the dict with Postgres. What changes in the HTTP contract?**
Ideally nothing — that is the point of the layering. What changes underneath:
`version` becomes a real optimistic-lock column with `UPDATE ... WHERE
version = $1`; the unique name constraint becomes a partial unique index
rather than the current read-then-write race; the cursor becomes a
`WHERE (created_at, id) < ($1, $2) ORDER BY created_at DESC, id DESC LIMIT $3`
with a matching composite index; the idempotency table needs a TTL.

**Q14. Name a race condition in this code.**
Several real ones:
- `create_monitor` checks `find_one` for a duplicate name, then creates — not
  atomic across processes. Needs a DB unique constraint.
- Same for the idempotency lookup-then-create.
- `transition_status` reads the monitor, then updates, then creates alerts, in
  three separate lock acquisitions — two concurrent transitions can both open
  an alert.
The `RLock` only helps within one process; multiple gunicorn workers each have
their own store and rate-limit counters.

**Q15. The rate limiter is a fixed window. What is wrong with that, and what
would you use?**
A fixed window allows 2x the limit across a boundary — 100 requests at 0:59
and 100 more at 1:01. Alternatives: sliding-window log (accurate, more
memory), sliding-window counter (good approximation), or token bucket (allows
controlled bursts, which is usually what you actually want for an ingest API).
Also: the counter is per-process, so N workers means N times the limit; real
deployments push this to Redis or the edge/gateway.

**Q16. `/v1/series` takes up to 1000 points per request. How did you pick that,
and what happens at real Datadog volume?**
The number is arbitrary here — a strong candidate says so and asks what it
should be derived from (payload size, p99 handler latency, memory per worker).
At volume: the HTTP handler should do nothing but validate and enqueue
(Kafka), with aggregation downstream; you would add compression, possibly a
binary encoding, and you would care a great deal about **tag cardinality**,
since every distinct tag combination is a separate stored series. The 20-tag
cap in `validation.py` is a crude gesture at that problem.

**Q17. Partial success returns `202` with a mixed body. Argue against it.**
It is awkward: the client must inspect the body, not just the status, and
`202` with 999 rejected points is arguably a lie. Alternatives: all-or-nothing
(simpler, but one bad point wastes a whole batch — bad for agents that cannot
easily re-split), or `207 Multi-Status`. For telemetry, partial acceptance is
usually right because dropping 999 good points over 1 bad one is worse.

**Q18. How do you make this observable?** (Expect fluency here — it is Datadog.)
Currently: a request ID, a timing header, one access log line. Missing:
- **RED metrics** per route — Rate, Errors, Duration — as histograms, not
  averages, so p99 is visible.
- Status-code and error-`code` dimensions, so a 400 spike is distinguishable
  from a 500 spike.
- **Distributed tracing** — propagate the incoming trace context instead of
  minting a bare uuid; the current `X-Request-ID` does not join an existing
  trace. `traceparent` (W3C) or `x-datadog-trace-id`.
- **Structured** JSON logs with the request/trace ID as a field, so logs and
  traces correlate.
- Cardinality discipline on metric tags: never tag by `monitor_id` or raw
  path — use the route *template* (`/v1/monitors/<id>`), which is why Flask's
  `request.url_rule` matters.
- A readiness endpoint distinct from liveness.

**Q19. `/health` returns 200 unconditionally and touches no dependencies. Bug
or feature?**
Feature, for *liveness* — a health check that pings the database will fail
during a blip and get an otherwise-healthy container killed, amplifying an
outage. **Readiness** is the one that should check dependencies, and it should
be a separate endpoint. Strong answer draws the distinction unprompted.

**Q20. A customer reports their monitor "randomly reverted." How do you debug
it with what this API exposes?**
`X-Request-ID` from their report → the access log line → `updated_at` and
`version` on the monitor. Then the gap: there is no audit trail of *who*
changed what, and last-write-wins means a client that did not send `If-Match`
silently clobbered another. Fix: an append-only audit log, and consider making
`If-Match` mandatory on `PATCH`.

### Curveballs

**Q21. Make `GET /v1/monitors` 50x faster with 10 million monitors.**
Index on the filter columns; composite index matching the cursor's sort order;
push filtering into the database rather than Python; move free-text `q` to a
search index (Elasticsearch) instead of a `LIKE` scan; cache hot queries;
consider read replicas. Strong answer asks about the read/write ratio and the
actual filter distribution before optimizing anything.

**Q22. Add webhook notifications when an alert opens. Design it.**
Wants: asynchronous delivery (never block the transition on an HTTP call to a
customer endpoint), a queue with retries and exponential backoff, a dead-letter
queue, **at-least-once delivery** so receivers must be idempotent (send an
event ID), HMAC-signed payloads with a timestamp so receivers can verify and
reject replays, and a per-endpoint circuit breaker so one dead customer
endpoint does not back up the queue.

**Q23. Add a bulk `PATCH` for 500 monitors at once. What is the contract?**
Forces the partial-success conversation again, plus: does it need a
transaction? Is it idempotent? Should it be async — return `202` with a job ID
and a `GET /jobs/{id}` to poll? Above a few hundred items, async is usually the
right call.

**Q24. A tag value is user-controlled and unbounded. What could go wrong?**
Cardinality explosion — tagging by `user_id` or `request_id` creates a new time
series per value, which is the canonical way to melt a metrics backend and the
customer's bill. Also a storage and query-planner problem. Mitigations: caps
(the 20-tag limit), value-length limits, cardinality monitoring with alerts,
and rejecting or truncating at ingest.

**Q25. What would you delete from this codebase?**
Open-ended; tests taste. Reasonable answers: the `links` blocks (hypermedia
nobody consumes), the `q` search param (misleading at scale), the hand-rolled
validation (use pydantic), the `X-Response-Time-Ms` header (belongs in metrics,
not a header clients might depend on).

**Q26. `no_data` is a valid status. Trace what happens when a monitor
transitions into it.**
This is a planted gap, and the best candidates find it unprompted.
`transition_status` branches on `warn`/`alert` and then on `ok`; `no_data`
matches neither, so the status changes but no alert opens *and* any existing
open alert stays open forever. Ask what the right behavior is — it is a real
product question. "No data" often means the agent died, which is arguably more
urgent than a threshold breach, but alerting on it aggressively creates noise
during deploys. Whatever they choose, the follow-up is: how would you have
caught this? Answer: the status enum and the transition logic are two separate
sources of truth, and no test covers `no_data`.

---

## 3. What to look for

Weighted for a verbal round: how they reason out loud matters as much as the
conclusions, because the conclusions are all you would get from a written
test anyway.

**Strong signals**
- **Traces data through the system unprompted**, rather than describing
  endpoints as a disconnected list. The top signal in this round.
- Names asynchronous boundaries and says what is true on each side of them.
- Asks about clients and traffic shape before proposing a design.
- Distinguishes `400` / `401` / `403` / `404` / `409` / `413` / `422` / `429`
  precisely and can justify each.
- Reaches for idempotency unprompted when discussing retries.
- Treats errors as part of the contract, not an afterthought.
- Says "it depends" *and then picks one*, with the deciding factor named.
- Spots that the in-memory store breaks across workers.
- **Explains something complicated so it lands.** Checks whether you are
  following; adjusts when you are not. This is the job.

**Weak signals**
- Lists endpoints but cannot connect them into a flow, or cannot say what
  triggers the next step.
- Recites REST dogma without tradeoffs ("PUT is always correct").
- Cannot explain why offset pagination is a problem.
- Designs only the happy path.
- Treats `200 {"success": false}` as acceptable.
- Never mentions observability, auth, or limits unless pushed.
- Optimizes before asking what is slow.
- Vocabulary without mechanism — says "idempotent" and "eventually
  consistent" correctly but cannot say what concretely goes wrong without
  them.

**Calibration:** a candidate who gets Q0 right and half the rest wrong is a
better bet than one who nails the trivia and cannot trace a request. The
trivia is learnable in a weekend; the systems intuition is not.

## 4. Suggested 45-minute structure

Q0 is non-negotiable and goes first — it is the thing this round exists to
test, and a candidate who struggles there has told you most of what you need
to know while there is still time to explore why.

| Time | Segment |
|---|---|
| 0–5 | Candidate skims `README.md` — the endpoint table and conventions |
| 5–15 | **Q0, the data-flow trace**, plus its follow-ups |
| 15–22 | Warm-up (Q1–Q4) — confirm they can navigate the system |
| 22–33 | Core (pick 3–4 from Q5–Q12) — verbs, idempotency, pagination |
| 33–41 | Depth (pick 2 from Q13–Q20); lean on Q18 for this company |
| 41–45 | One curveball if there is room, then their questions |

Do not attempt all 26. Six to eight questions with real follow-ups beat a
survey — and since nothing is being written down, follow-ups are the only way
to tell recall from understanding.

**If time runs short, cut in this order:** curveballs, then depth, then
warm-up. Never cut Q0.

## 5. Verbal design exercises

No code is written in this round, so these are posed as "talk me through it."
Each takes 5–10 minutes and has a concrete right-ish answer, which makes them
better than open-ended architecture chat.

1. **"Add sorting: `?sort=name&order=asc`. What breaks?"**
   The cursor currently encodes `(created_at, id)`. Change the sort and the
   cursor must encode the *active* sort key, or paging silently returns wrong
   results. Strong answers also note the cursor must record which sort it was
   issued for, and reject a cursor replayed against a different sort.

2. **"Make `If-Match` mandatory on `PATCH`. Walk me through the rollout."**
   Tests migration thinking, which pure design questions miss. Wants: the new
   status code (`428 Precondition Required`), a deprecation window where the
   header is optional but its absence is logged, client communication, and a
   metric to watch the proportion of requests still omitting it.

3. **"Design `GET /v1/monitors/{id}/history`."**
   There is no audit trail today. Forces them to invent one: what is an event,
   is it append-only, do you store diffs or snapshots, how does it paginate,
   and how long is it retained.

4. **"Same idempotency key, different request body. What should happen?"**
   The current code returns the original resource — which is wrong. The right
   answer is `422`, because the client has a bug and silently returning
   something unrelated hides it. Requires storing a body fingerprint next to
   the key.

5. **"Design bulk `PATCH` for 500 monitors."**
   Re-opens partial success, plus: is it transactional? Idempotent? Above a
   few hundred items, should it be async — `202` with a job ID and a
   `GET /jobs/{id}` to poll? Listen for them asking about the batch size
   distribution before choosing.

6. **"You're on call. p99 latency on `GET /v1/monitors` just tripled. Go."**
   Not a design question — a debugging one, and a good closer. Wants: check
   whether it is all routes or one, correlate with a deploy, look at the
   rate-limit and error-rate dimensions, check whether one tenant's traffic
   shape changed, and only then look at the code. Candidates who immediately
   start optimizing SQL without establishing what changed are showing you how
   they behave in an incident.

---

## 6. Prep notes for the candidate side

If this repo is being used to *practice* rather than to interview, the highest-
return preparation is:

1. **Be able to deliver Q0's trace cold, in under three minutes**, out loud,
   without the diagram. That is the reported hurdle.
2. **Have one sentence ready for each of:** why `202` not `201`; why `PATCH`
   not `PUT`; why cursors not offsets; why `404` not `403`; why `409` exists;
   what `Idempotency-Key` protects against.
3. **Practice the "what breaks?" reflex.** Every design choice here has a
   failure mode, and naming it unprompted is the single clearest signal of
   experience.
4. **Say "it depends," then pick one** and name the deciding factor. Refusing
   to choose reads as evasion; choosing without a reason reads as dogma.
