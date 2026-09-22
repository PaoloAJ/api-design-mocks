# Interviewer Guide — mock-5, Builds API

Everything you need to run the session. The candidate never sees this file.

**The candidate gets:** the `app/` tree, `SPEC.md`, and `PR.md` (handed over at
minute 27). **No README, no search, no code written.** They read and talk; you
probe.

The app is ~1,100 lines of Flask across 12 files with an in-memory store, and
**no authentication** — it sits behind a gateway that terminates auth and
passes a project slug in the path. That removes a layer of ceremony so the
whole hour goes to data flow and API design.

---

## 0. Format

| Phase | Minutes | What happens |
|---|---|---|
| Background | 0–20 | Their project, what they want from the internship |
| 1. Explore the codebase | 20–33 | **§2 trace question.** Non-cuttable. |
| 2. Design a feature | 33–46 | §3 — pick two |
| 3. Review the PR | 46–59 | Hand them `PR.md` and `SPEC.md`; §4 answer key |

Say out loud at the start: *"You won't write any code. Take three minutes to
look around, then I'll ask you to explain how a build moves through this
system. There's no README — the file tree is the map."*

---

## 1. Dataflow summary — the answer key for phase 1

### Request lifecycle

```
HTTP request
  │
  ├─ before_request: assign_request_id
  │     X-Request-ID from the client, or a fresh uuid4; start the timer
  │
  ├─ before_request: resolve_project
  │     parses /v1/projects/<slug>/... -> g.project
  │     unknown slug -> 404 project_not_found
  │     NOT auth: there is none. The gateway owns that.
  │
  ├─ before_request: enforce_rate_limit
  │     fixed window, 120 req / 60s, keyed by client IP
  │     /healthz skips it
  │
  ├─ route handler
  │     validate body    -> 400 with EVERY field error collected
  │     check scope      -> 404 if another project owns it
  │     check version    -> 409 if If-Match is stale
  │     read/write store (RLock held for the critical section)
  │     serialize        (storage shape -> wire shape)
  │
  ├─ errorhandler (if anything raised)
  │     APIError / HTTPException / bare Exception -> one JSON envelope
  │
  └─ after_request: attach_headers
        X-Request-ID, X-RateLimit-*, X-Response-Time-Ms, access log line
```

`resolve_project` runs **before** the rate limiter so the counter keys on a
request we've already decided is addressable. Both are `before_request` hooks,
not per-route decorators — a new endpoint is then scoped by default, and nobody
ships a hole by forgetting a line.

### Build state machine

The only writer of `state` is `POST /builds/{id}/transition`. Jobs are a side
effect; a client never creates one.

```
   queued ──────> running ──────> passed
                     │       └───> failed
                     └───────────> cancelled

transition INTO running        -> stamp started_at, open one job per name
transition INTO passed|failed  -> stamp finished_at, close every running job
transition INTO cancelled      -> ** falls through both branches **
transition to the same state   -> no-op, 200, creates nothing
transition OUT of a terminal   -> 409 invalid_state_transition
```

**`cancelled` is the planted gap.** It is a valid target in `BUILD_STATES` and
in `TERMINAL_STATES`, it passes validation, it returns `200` — and it stamps no
`finished_at` and leaves every job `running` forever. It is reachable,
untested, and invisible unless you read the enum against the branches in
`app/api/builds.py:transition_build`. See Q7.

### Ingest path (`POST /v1/events`)

Deliberately unlike the CRUD resources:

```
batch of up to 500 runner events
  │
  ├─ > 500 items      -> 413 payload_too_large
  ├─ validate each item independently
  │     build_id, kind in {job_started, job_finished, log_chunk},
  │     timestamp within -6h .. +5m
  │
  ├─ >= 1 accepted -> 202 {accepted, rejected, errors: [{index, reason}]}
  └─ 0 accepted    -> 400 (same body shape)
```

`202`, not `201`: the batch is acknowledged as received, but **nothing has been
applied to a build yet** — a worker drains `_queued` later. Errors carry the
`index` so a client maps a failure back to what it sent.

### The two deliberate gaps

Strong candidates notice and ask. Weak ones narrate them as if they exist.

1. **Nothing consumes `_queued`.** Events are accepted and dropped on the
   floor. There is no worker.
2. **Nothing actually runs a build, and nothing notifies anyone.** Transitions
   are driven entirely by an external caller.

---

## 2. The trace question — ask this first, always

**Non-cuttable.** Budget 10 minutes. It is worth more than any three others.

> **"A developer pushes a commit to the `web` project. CI queues a build, runs
> two jobs, one fails, and the developer hits rerun. Walk me through every hop
> — what data moves, what shape it's in, and what the system does at each
> step."**

A complete answer traverses:

| Step | What they should say |
|---|---|
| 1 | `POST /v1/projects/web/builds` with branch + commit → `201`, `Location`, `ETag`, state `queued` |
| 2 | Middleware first: request ID → `resolve_project` sets `g.project` → rate limit |
| 3 | `validate_build_create` collects **all** field errors, not the first |
| 4 | `store.create` assigns `bld_*`, `created_at`, `version: 1` under the lock |
| 5 | `POST /builds/{id}/transition {"state": "running"}` → stamps `started_at`, **creates one job per name** |
| 6 | Runner reports progress via `POST /v1/events` → **`202`**, async boundary, not yet applied |
| 7 | `transition {"state": "failed"}` → stamps `finished_at`, closes every running job as `failed` |
| 8 | Rerun: there is **no rerun endpoint in `main`** — this is the seam PR #77 fills |

**Grading bands**

| Band | Looks like |
|---|---|
| **Weak** | Lists endpoints without connecting them. Can't say what triggers the next step. Thinks the app runs the builds. |
| **Adequate** | Gets the happy path end to end. Names the middleware. Knows jobs come from a transition, not from the client. |
| **Strong** | Names the `202` as an async boundary *and says what's true on each side of it*. Notices nothing consumes `_queued`. Asks who calls `transition` and realizes it's external. Spots that rerun has no home yet. |

**Four escalating follow-ups** — ask until they stop being able to answer:

1. *"The runner posts 400 events and we return 202. The developer immediately
   GETs the build. What do they see?"* → The build is unchanged. `202` promised
   receipt, not application. A client that expects otherwise has misread it.
2. *"Two runners transition the same build to `passed` at the same instant.
   What happens?"* → Second one hits the `previous == target` no-op and returns
   200 without duplicating jobs. If they instead reach for `If-Match`, ask why
   the transition endpoint doesn't require it.
3. *"Where would you put the retry if the event worker crashes mid-batch?"* →
   Wants: the queue needs at-least-once delivery, so event application must be
   idempotent, keyed on something stable. Good answers note events have no ID
   today.
4. *"We run four workers behind a load balancer. What breaks?"* → The store is
   process-local. Each worker has its own builds, its own idempotency table,
   its own rate-limit counters. This is the single best "did they read the
   code" check — `store.py`'s docstring says it outright.

---

## 3. Design questions — phase 2, pick two

Each has a concrete right-ish answer, which beats open architecture chat. The
best two have a **hidden coupling**; finding it *is* the answer.

### D1 — "Add `?sort=branch`. What breaks?" ★ hidden coupling

The cursor encodes `(created_at, id)` and `paginate` walks records assuming
that exact ordering. Change the sort and the cursor must encode the **active**
sort key, or paging silently skips and repeats rows. Strong answers add: the
cursor must record *which* sort issued it and reject a replay against a
different one — that is **SPEC rule 7**, already written down.

### D2 — "Design `GET /builds/{id}/logs` for a build still running." ★ coupling

Forces streaming vs polling. Wants: logs are append-only and unbounded, so not
a JSON array; either chunked transfer / SSE, or a cursor-paginated
`?after=<offset>` that the client polls. Then the real question — **what is the
sort key?** Log lines share timestamps, so it needs a monotonic sequence, which
is exactly the immutability point in **SPEC rule 8**.

### D3 — "Same `Idempotency-Key`, different request body. What should happen?"

Today `create_build` returns the original build — which is wrong. The right
answer is `422`: the client has a bug, and silently returning an unrelated
resource hides it. Requires storing a body fingerprint next to the key. Also
missing: no TTL, no in-flight handling, not scoped to a project. All four gaps
are deliberate.

### D4 — "Make cancelling a build actually work."

Straight into the planted gap. A good answer stamps `finished_at`, closes the
jobs as `cancelled`, and asks whether a cancelled job should be distinguishable
from a failed one. A great one asks **who** cancels and whether the runner
needs to be told — the API can mark it cancelled but nothing stops the work.

---

## 4. PR answer key — phase 3

Hand them **`PR.md` and `SPEC.md`** at minute 46. Say: *"This is up for review.
Tell me what's off and why it matters."*

**Seven defects. The mix matters more than the count** — see the ranking note
below.

| # | Tier | Defect | File | Breaks |
|---|---|---|---|---|
| **D3** | **Deep/security** | Project scope read from `X-Ci-Project-Id` header | `builds.py` `list_builds` | **Rule 3** |
| D1 | Surface | `_queue_position` loads + sorts the whole project on a single GET | `builds.py` `_queue_position` | **Rule 1** |
| D2 | Surface | `branch` filter moved to *after* pagination | `builds.py` `list_builds` | **Rule 2** |
| D4 | Mid | `rerun` writes `state` outside the transition endpoint | `builds.py` `rerun_build` | **Rules 5, 6** |
| D5 | Mid | `sibling_count` re-scans every job, once per job (N+1) | `jobs.py` `list_build_jobs` | Rule 1 (spirit) |
| D6 | Deep | `rerun` idempotency key is global — returns a *different* build with `200` | `builds.py` `rerun_build` | — |
| D7 | Mid | `_queue_position` sorts on `created_at` with no tiebreaker | `builds.py` `_queue_position` | **Rule 8** |

### The ranking signal — this is the best discriminator in the case

> A candidate who lists seven defects flat has reviewed a diff.
> A candidate who **leads with D3 and says why it outranks the rest** has done
> code review.

D3 is four lines, wears a comment claiming an internal proxy sets the header,
and hands any client another project's builds. Everything else is a
correctness or performance bug; D3 is a data breach. The slide asks for "what's
off **and why it matters**" — that second clause is exactly this.

If they miss it entirely, prompt once: *"Anything here you'd block the merge
over?"* Missing it after that prompt is a significant negative.

### Reproductions — all verified

Run from the PR head. Output below is real, not illustrative.

```
D1 — queue_position scans the whole project on GET /builds/{id}
builds loaded and sorted to compute one integer: 200

D2 — branch filter runs after pagination -> short pages, wrong has_more
30 release builds exist
asked for up to 10 release builds; got : 0
has_more claims                        : True
-> an empty page with has_more=true; the client must walk every page

D3 — X-Ci-Project-Id header overrides the project scope
caller asked for project 'web' with a spoofed header
branches returned                      : ['secret']
-> any client reads any project's builds by setting one header

D4 — rerun re-queues a terminal build with no transition validation
build state after rerun                : queued
jobs still attached, state             : ['passed']
-> a 'queued' build carries finished jobs from the previous run

D5 — sibling_count re-scans every job, once per job (N+1)
store.list('jobs') calls for 20 jobs   : 21

D6 — rerun Idempotency-Key is global -> returns a DIFFERENT build, 200
rerun build ONE  -> returned branch    : one
rerun build TWO  -> HTTP               : 200
rerun build TWO  -> returned branch    : one
build TWO actual state (never rerun)   : failed
-> 200 OK, wrong resource, build TWO silently never re-queued

D7 — queue_position sorts on created_at alone -> unstable positions
creation order positions               : [3, 4, 1, 2, 5, 6]
expected                               : [1, 2, 3, 4, 5, 6]
```

**D6 is the excellence test.** It returns `200` with a plausible body, the
tests pass, and the caller has no way to know their build was never re-queued.
The key isn't scoped to the build ID, so the nightly retry cron reruns exactly
one build and reports success for all of them. A candidate who finds this has
read the rerun handler properly rather than skimming for smells.

### The changed test is itself a signal

`tests/test_pagination.py` renames `test_filter_runs_before_pagination` and
**deletes the two assertions that would have caught D2**. The PR description
explains it away as a retitle. A changed test usually means a changed contract;
ask *"why did that test need to change?"* if they don't raise it themselves.

### False positives — do not reward flagging these

| Looks wrong | Actually fine |
|---|---|
| `_rerun_payload` returns fields already on `build` — pure ceremony | It's the right seam. Reruns are about to diverge from the original build, and the docstring says so. Flagging it is defensible; **insisting** on deleting it is not. |
| `paginate(records, limit=limit, cursor=...)` in `list_build_jobs` takes defaults | The defaults are correct here — jobs use the same `(created_at, id)` ordering as everything else. |
| `rerun` returns `200`, not `201` | Correct. Nothing is created — the build is re-queued in place, which is the whole point of keeping the ID. |

A candidate who flags all three has not shown judgment. Reviewing has a
false-positive cost; say so if they carpet-bomb.

---

## 5. Signals

**Strong — concrete behaviors, not adjectives**
- Traces data through the system unprompted instead of listing endpoints.
- Says what is true on *each side* of the `202` boundary.
- Asks *"who calls this?"* about `transition` and finds it's external.
- Reads the enum against the branches and finds `cancelled`.
- Asks how pagination works **before** designing sorting (D1).
- Leads the PR review with D3 and explains why it outranks the others.
- Notices the deleted assertions in `test_pagination.py`.
- Says "it depends" *and then picks one*, naming the deciding factor.
- Spots that the in-memory store breaks across workers.

**Weak**
- Narrates the two deliberate gaps as if they're implemented.
- Thinks this service runs the builds.
- Lists PR defects flat with no severity ordering.
- Can't say why offset pagination is a problem.
- Treats `200 {"success": false}` as acceptable.
- Recites REST dogma without tradeoffs ("PUT is always correct").
- Vocabulary without mechanism — says "idempotent" correctly but can't say
  what concretely goes wrong without it.

**Calibration:** a candidate who nails the trace and half the rest is a better
bet than one who gets the trivia and can't trace a request. The trivia is
learnable in a weekend; the systems intuition is not.

---

## 6. Timing and cut order

| Time | Segment | Cuttable? |
|---|---|---|
| 0–20 | Background | Shorten to 15 if they're concise |
| 20–33 | Trace question + follow-ups | **Never** |
| 33–46 | Two design questions | Drop to one |
| 46–59 | PR review | Drop to D3 + two others |
| 59–60 | Their questions | Keep 2 min |

**If you're running long, cut in this order:** second design question → the
trace's follow-up #4 → PR defects beyond D3/D1/D2. **Never cut the trace
question**, and never cut D3 from the PR review — those two are what the round
exists to test.

---

## 7. Verification record

Measured, not estimated, on the committed case:

| Check | Result |
|---|---|
| `pytest -q` on `main` | ✅ 57 passed |
| `pytest -q` with PR applied | ✅ 63 passed (matches `PR.md`) |
| `python wsgi.py` | ✅ starts on 5001, first try |
| App code | 1,118 lines, 12 files under `app/`, depth 1 |
| Dependencies | 2 (flask, pytest) |
| `git apply --check --directory=mock-5` from repo root | ✅ clean |
| Planted gap (`cancelled`) | ✅ reachable, returns 200, **untested** |
| All 7 PR defects | ✅ reproduced by script; output in §4 |
| Candidate-facing README | ✅ none |
