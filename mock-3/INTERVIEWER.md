# Interviewer Guide — Content Moderation API (mock-3)

The only document you need during the session. The candidate gets three files:
the app, `SPEC.md`, and `PR.md`. **There is no README and they have no search.**
Everything they are told about what this service does comes out of your mouth.

The repo is ~1,630 lines of Flask across 12 files with an in-memory store, and
58 tests. Small enough to read in ten minutes, opinionated enough to argue
with.

**What to say at minute 0** (they cannot read this anywhere):

> "This is a content moderation backend. Platforms send us user content, it
> lands in a review queue, a human reviewer decides to approve or remove it,
> and the user whose content was removed can appeal. Take three minutes, look
> around, then I'll ask you to explain how data moves through it."

---

## 0. Format

| Phase | Minutes | What happens |
|---|---|---|
| Background | ~20 | Their projects, what they want from the internship |
| 1. Explore the codebase | ~13 | **Q0**, the trace question, plus follow-ups |
| 2. Design a feature | ~13 | Two or three from §4 |
| 3. Review a PR | ~13 | `PR.md`, answer key in §5 |

**No code is written.** They talk, you probe. A rehearsed definition and real
understanding sound identical for about one sentence, so the follow-up is where
the signal is — ask "why?" or "what breaks?" twice.

---

## 1. Dataflow summary

### Request lifecycle

```
HTTP request
  │
  ├─ before_request: assign_request_id
  │     X-Request-ID from the client, or a fresh uuid4; start the timer
  │
  ├─ before_request: authenticate
  │     /health and / skip this
  │     X-Mod-Key header (or Bearer token) -> platform_id + role in flask.g
  │     missing -> 401 missing_api_key    unknown -> 401 invalid_api_key
  │
  ├─ before_request: enforce_rate_limit
  │     fixed window, 100 req / 60s, keyed by API key
  │     over -> 429 + Retry-After
  │
  ├─ route handler
  │     validate body      -> 400 with every field error collected
  │     check ownership    -> 404 if another platform owns it
  │     check role         -> 403 if the credential may not decide
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

Auth precedes rate limiting so the counter keys on a *known* identity;
otherwise an unauthenticated flood spends some real tenant's budget. Both are
`before_request` hooks, not per-route decorators — a new endpoint is protected
by default, and nobody ships an auth bypass by forgetting a line.

**Two roles.** `reviewer` may decide cases; `ingest` may submit content and
gets `403 insufficient_role` on a decision. That 403 is the deliberate contrast
with the 404 used for another tenant's case.

### Case state machine

The only writer of `state` is `POST /v1/cases/{id}/decision`. `PATCH` cannot
touch it.

```
                    ┌──────────────────────────────┐
                    │                              │
  submitted ──> pending ──> in_review ──> approved  │
                    │            │                  │
                    │            └────> removed ────┤
                    │                      │        │
                    │                      │   appeal overturned
                    │                      ▼        │
                    │                   appeal ─────┘
                    │
                    └────> escalated   <-- accepted, but see Q9: PLANTED GAP

decision "approved" -> state approved, reason cleared to "none"
decision "removed"  -> state removed, reason REQUIRED, queue reset to standard
decision on an already-approved/removed case -> 409 already_decided
```

`TERMINAL_STATES = {approved, removed}`. Note what is *not* in that set.

### Appeal lifecycle

```
open ──resolve upheld──────> upheld      (case stays removed)
  │
  └───resolve overturned──> overturned   (case -> approved, reason -> none)

appeal a case that was never decided -> 409 not_decided
a second open appeal on one case     -> 409 duplicate_appeal
resolve an already-resolved appeal   -> 409 invalid_state_transition
```

### Ingest path (`POST /v1/submissions`)

Deliberately unlike the CRUD resources:

```
batch of up to 500 items
  │
  ├─ > 500 items           -> 413 payload_too_large
  ├─ validate each item independently (external_id, kind, text)
  │
  ├─ >= 1 accepted -> 202 {accepted, rejected, items:[...], errors:[{index, reason}]}
  └─ 0 accepted    -> 400 (same body shape)
```

`202`, not `201`: the content is durably recorded, but **no case exists yet**.
Nothing has classified it, assigned a queue, or made it reviewable. Promising
`201` would promise a URL the client could `GET`.

### The two deliberate gaps

Neither is implemented, and both are named in `SPEC.md` under Non-goals:

1. **Classification.** Ingest writes an `item` with `score: None` and stops.
   Something must score content and open a case; nothing here does.
2. **Reviewer notification.** A case sits in a queue and nothing tells a human
   it is there.

Strong candidates notice and ask. Weak ones narrate them as if they exist.
**This is one of the best signals in the case.**

---

## 2. Q0 — the trace question (NON-CUTTABLE, ask first)

Budget 8–10 minutes. This is what the round exists to ask.

> **"A user posts something. Another user reports it. A reviewer removes it.
> The original poster appeals and wins. Walk me through every hop — what data
> moves, what shape it is in, and what the system does at each step."**

Let them talk. Interrupt only to unstick them. A complete answer traverses:

```
platform ──POST /v1/submissions──> ingest
                                     │  auth, rate limit, per-item validation
                                     │  202 Accepted (async: no case yet)
                                     ▼
                              [classifier]        <- NOT in this repo; ask anyway
                                     │  scores content, assigns a queue
                                     ▼
                        POST /v1/cases  (or a user report)
                                     │  state: pending
                                     ▼
                              [reviewer notification]  <- NOT here; ask anyway
                                     │
                                     ▼
   reviewer ──POST /v1/cases/{id}/decision {"decision":"removed"}──>
                                     │  role checked, reason REQUIRED,
                                     │  decided_by + decided_at stamped,
                                     │  case becomes terminal
                                     ▼
   poster ──POST /v1/cases/{id}/appeals──> appeal state: open
                                     │
                                     ▼
   reviewer ──POST /v1/appeals/{id}/resolve {"outcome":"overturned"}──>
                                        appeal: overturned
                                        case:   back to approved
```

**Grading:**

| Band | What it sounds like |
|---|---|
| **Weak** | Lists endpoints without connecting them. Cannot say what triggers the next step. Thinks `POST /v1/submissions` creates a case. |
| **Adequate** | Connects the hops in order, knows which endpoint does what, gets the appeal loop right. |
| **Strong** | Names the **async boundary** (why `202`, not `201` — content is recorded but no case is reviewable), spots **both missing pieces** (classifier, notification) and asks about them rather than pretending, and distinguishes where state actually changes from where data merely passes through. |

**Follow-ups, escalating:**

1. *"Where can this lose data, and does that matter?"* — The `202` window: we
   acknowledged content but nothing has classified it. Unlike telemetry, losing
   moderation content is a compliance problem, not an acceptable trade. Good
   candidates draw that contrast themselves.
2. *"The classifier is down for an hour. What does a user see?"* — Content is
   accepted (`202`) and silently never reviewed. Nothing surfaces the backlog.
   Leads to queues, dead-letter handling, and a staleness alert.
3. *"Two reviewers open the same case at the same time and both decide."* —
   First wins, second gets `409 already_decided`. Ask what `in_review` is
   supposed to do about it — nothing claims a case today.
4. *"Where would you put a queue, and why there?"* — Between ingest and
   classification. Ingest should validate, record, and enqueue, nothing more.

---

## 3. Warm-up questions (2 min each, after Q0)

**Q1. Walk me through `GET /v1/cases?queue=priority`.**
Middleware chain (request ID → auth → rate limit), then tenant scope → filter →
paginate → serialize. Strong: notices filtering happens *before* pagination and
says why the reverse is wrong (short pages, wrong `has_more`); notices
`serialize()` exists so storage and wire shapes can diverge.

**Q2. Why `PATCH` and not `PUT`?**
`PUT` replaces the whole resource, so the client must echo back server-owned
fields (`state`, `version`, `decided_at`) and risks clobbering what it does not
know about. Strong: `PUT` is idempotent, `PATCH` is not inherently so; mentions
JSON Merge Patch vs JSON Patch and the "how do I null a field?" ambiguity.

**Q3. Why does `POST /v1/cases/{id}/decision` exist instead of `PATCH`ing
`state`?**
The transition has side effects — it stamps the reviewer, closes the case, and
enforces that removals carry a reason. Hiding that behind a field write makes
`PATCH` unpredictable. Strong: generalizes — when an update is an *event*
rather than an assignment, give it its own endpoint. `SPEC.md` rule 5.

**Q4. Why is another tenant's case a `404` but an ingest key's decision a
`403`?**
`404` because `403` would confirm the ID exists, letting an attacker enumerate
across tenants. `403` is right for the role check: we know who they are, the
resource is legitimately theirs, and nothing leaks. `SPEC.md` rule 4.

---

## 4. Design questions — task 2 (pick 2–3)

Each has a concrete right-ish answer, which beats open architecture chat.

**D1. "Add `?sort=` to the case list. What breaks?"**
*The hidden coupling, and finding it is the answer.* The cursor encodes
`(created_at, id)`. Change the sort and the cursor must encode the *active*
sort key, or paging silently returns wrong results. Strong answers add that the
cursor must record which sort issued it and reject a replay against a different
ordering — which is `SPEC.md` rule 7, already written down.

**D2. "Reviewers claim a case before working it. Design that."**
Wants: a `POST /v1/cases/{id}/claim` action sub-resource (not a `PATCH`), a
claim that expires so a reviewer who closes their laptop does not park a case
forever, `409` when someone else holds it, and the observation that `in_review`
already exists as a state with nothing that sets it. Ask how the TTL is
enforced without a scheduler.

**D3. "Build the classifier hand-off."**
The deliberate gap. Wants: a queue between ingest and scoring, at-least-once
delivery so scoring must be idempotent, what happens to content that fails
scoring repeatedly (dead-letter → human queue, never silently dropped), and
whether the score arrives as a `PATCH` from the classifier or as a new case.

**D4. "Same idempotency key, different request body. What should happen?"**
The current code returns the original resource — which is wrong. The right
answer is `422`: the client has a bug, and silently returning something
unrelated hides it. Requires storing a body fingerprint next to the key.

**D5. "Make `If-Match` mandatory on `PATCH`. Walk me through the rollout."**
Tests migration thinking. Wants `428 Precondition Required`, a deprecation
window where the header is optional but its absence is logged, client
communication, and a metric watching the proportion still omitting it.

---

## 5. PR answer key — task 3

Hand them `PR.md` and `SPEC.md`. Say: *"This is up for review. Tests pass. Tell
me what's off and why it matters."*

**That second clause is the whole test.** A candidate who lists six defects flat
has reviewed a diff. One who leads with the security defect and says why it
outranks the others has done code review.

### Defects

| # | Tier | Location | Breaks |
|---|---|---|---|
| D1 | Mid | `list_cases`, `X-Mod-On-Behalf-Of` | **SPEC rule 3** |
| D2 | Surface | `list_cases`, `exclude_claimed` | **SPEC rule 2** |
| D3 | Surface | `_queue_position` | **SPEC rule 1** |
| D4 | Mid | `decide_batch`, no role check | — |
| D5 | Mid | `decide_batch`, `decided_by` on every case | SPEC rule 6 (spirit) |
| D6 | **Deep** | `decide_batch`, `store.get` raises mid-loop | — |

---

**D1 — Tenant scoping from a request header. `SPEC.md` rule 3. SECURITY.**

```python
on_behalf_of = request.headers.get("X-Mod-On-Behalf-Of")
platform_id = on_behalf_of or g.platform_id
```

Four lines, arriving in an unrelated third commit with a sympathetic comment
about vendor onboarding. Any customer can send that header and read any other
platform's moderation queue — reported content, reasons, reviewer names.

*Reproduce:*
```
alpha's own cases:        0
alpha + spoofed header:   1  <-- reads plat_beta
    leaked text: 'content beta_secret'
```

*Strong candidate says:* this is the one that matters, and says why — it is a
cross-tenant data breach reachable by anyone who guesses a header name, versus
five bugs that produce wrong numbers. Notices the comment claims a gateway sets
the header and asks what stops a client from sending it directly. Correct fix:
scope from `g.platform_id`, and authorize vendor access as a property of the
credential, not a header.

**This is the severity test. Rank it first or you have not done code review.**

---

**D2 — Filtering after pagination. `SPEC.md` rule 2.**

```python
page, next_cursor = paginate(records, ...)
if request.args.get("exclude_claimed") == "true":
    page = [c for c in page if c["state"] != "in_review"]
```

The filter runs on the sliced page, so a client asking for 3 gets fewer, and
`has_more` describes the pre-filter set.

*Reproduce:* `limit=3 -> returned 2 rows, has_more=True`

*Strong candidate says:* the page shrinks unpredictably, a client paging to fill
a screen never knows how many more to ask for, and a filter selective enough can
return an empty page with `has_more: true` forever. The comment ("so we only
check the rows we return") states the bug as if it were an optimization.

---

**D3 — Full scan to answer a single-record GET. `SPEC.md` rule 1.**

```python
peers = [c for c in store.list("cases") if ... ]
peers.sort(key=lambda c: (c["created_at"], c["id"]))
```

Loads and sorts the platform's entire queue to compute one integer, on the
hottest endpoint in the app.

*Reproduce:* `store.list() calls for ONE case detail: 1` — one call that returns
all 200 peers and sorts them, per detail view.

*Strong candidate says:* this is O(n log n) per case-open, it is the endpoint a
reviewer hits most, and it gets worse exactly when the queue is deepest — i.e.
during the incident. Also that `queue_position` is unstable: it changes under
the reviewer as cases arrive, so the number is wrong by the time it renders.

---

**D4 — The batch endpoint skips the role check.**

The single-case handler has `if g.role != "reviewer": raise ForbiddenError`.
`decide_batch` has no such check, so an ingest-only credential can decide cases
in bulk — the exact action the single path forbids.

*Reproduce:*
```
single-case decision as ingest key: 403 insufficient_role
SAME action via batch endpoint:     200 decided=1  <-- role check bypassed
final state: removed
```

*Strong candidate says:* a new endpoint bypassing an existing authorization
check is the classic way privilege escalation ships, and this is the argument
for enforcing authorization in middleware (or a shared helper) rather than
per-handler — which is the same reasoning the codebase already applies to auth.

---

**D5 — Every case in the batch is stamped with one reviewer.**

Defensible-looking, but it means one click attributes fifty removals to a
reviewer who saw a list, not fifty pieces of content. `SPEC.md` rule 6 requires
a recorded reviewer for audit; this satisfies the letter and guts the intent.

*Strong candidate says:* ask what the audit log is *for*. If it exists to defend
a removal to a regulator or the user, "reviewer_kim removed 50 items in one
request" is a different claim than fifty reviews. Wants the batch recorded as a
batch.

---

**D6 — DEEP: a partially applied batch reported as a total failure.**

```python
for index, case_id in enumerate(case_ids):
    case = store.get("cases", case_id)   # raises NotFoundError
```

`store.get` **raises** on an unknown ID. Every other failure in the loop is
collected into `errors` and skipped — but this one escapes the handler, the
error handler turns it into a `404`, and the response never mentions that the
cases *before it in the list* were already decided and persisted.

*Reproduce:*
```
batch with one bad id: 404 not_found
    first case state after the 404: removed  <-- partially applied, not reported
```

*Why this is the deep one:* the endpoint's own tests pass, the response is a
plausible `404`, and the client's only reasonable reaction — retry the batch —
hits `409 already_decided` on the cases that silently succeeded. A reviewer sees
a failure and has no idea content was removed. **Removals are not reversible
without an appeal.**

*Strong candidate says:* names the inconsistency first — one failure mode
raises while every other one is collected — then works out the consequence.
Asks the real design question: is this endpoint atomic or partial? It is
currently neither, which is the worst option. Either wrap it in a transaction
or catch `NotFoundError` and report it per-index like the others.

### False positives — do NOT reward flagging these

1. **`payload = validate_decision(body.get("decision") or {})`** with the
   comment about the two paths not drifting. This looks like indirection for
   its own sake; it is the right seam. Sharing the validator is what keeps the
   batch and single-case contracts identical. A candidate who calls this
   duplication-avoidance-gone-wrong has it backwards.

2. **`decide_batch` returns `200`, not `202`.** Looks inconsistent with the
   ingest endpoint's `202`. It is correct: the work is done synchronously and
   completely before the response: there is no async boundary here. A candidate
   who flags it has pattern-matched on "batch" instead of reading.

3. **`test_queue_position` asserts `sorted(seen) == [0,1,2]` rather than a
   fixed index.** Looks like a weakened assertion hiding a bug. It is correct:
   cases created in the same millisecond are ordered by the `id` tiebreaker, so
   pinning an index would be asserting a UUID coincidence.

**Reviewing has a false-positive cost.** A candidate who flags everything has
not shown judgment. Note who hedges and who commits.

---

## 6. Q9 — the planted logic gap (ask if they have not found it)

> **"`escalated` is a valid decision. Trace what happens to a case when a
> reviewer escalates it."**

This is a gap in the *base* codebase, not the PR. The best candidates find it
unprompted while exploring.

`decide_case` branches on `removed`, then on `approved`. **`escalated` matches
neither**, and `TERMINAL_STATES` is `{approved, removed}` — so escalating:

- stamps `decided_by` and `decided_at` as though the case were closed,
- leaves the case **non-terminal**, so any reviewer can re-decide it and
  silently overwrite the audit trail,
- never moves it to the `legal` queue, so no second reviewer ever finds it.

*Reproduce:*
```
after escalate: 200 state=escalated queue=priority decided_by=reviewer_kim
re-decide:      200 -> state=approved decided_by=reviewer_OTHER
legal queue:    0 cases
```

A case escalated for a second opinion is stamped as decided, invisible to the
people who should see it, and silently re-decidable by anyone.

*Follow-up:* **"How would you have caught this?"** The answer is the point: the
`DECISIONS` enum and the transition branches are two separate sources of truth,
and no test covers `escalated`. Reach for an exhaustive match, or a test that
iterates the enum.

---

## 7. Signals

**Strong**
- **Traces data through the system unprompted** rather than listing endpoints.
  The top signal in this round.
- Names the async boundary and says what is true on each side of it.
- Notices the two missing pieces (classifier, notification) and asks.
- **Leads the PR review with the security defect and justifies the ranking.**
- Distinguishes `400`/`401`/`403`/`404`/`409`/`413`/`422`/`429` precisely.
- Asks what the audit log is *for* before judging D5.
- Says "it depends" *and then picks one*, naming the deciding factor.
- Spots that the in-memory store breaks across workers.
- Checks whether you are following, and adjusts when you are not.

**Weak**
- Lists endpoints but cannot say what triggers the next step.
- Reads `POST /v1/submissions` as creating a case.
- Lists PR defects flat, with no severity ordering.
- Flags the false positives with the same confidence as the real defects.
- Recites REST dogma without tradeoffs ("PUT is always correct").
- Designs only the happy path.
- Never mentions authorization, observability, or limits unless pushed.
- Vocabulary without mechanism — says "idempotent" correctly but cannot say
  what concretely goes wrong without it.

**Calibration:** a candidate who nails Q0 and half the rest is a better bet than
one who knows the trivia and cannot trace a request. Trivia is learnable in a
weekend; systems intuition is not.

---

## 8. Timing and cut order

| Time | Segment |
|---|---|
| 0–3 | They skim the tree. You describe the domain out loud (see top of this file). |
| 3–13 | **Q0 + follow-ups** |
| 13–17 | Warm-up: two of Q1–Q4 |
| 17–28 | Design: two from §4 (D1 is the best single choice) |
| 28–41 | PR review |
| 41–45 | Q9 if unfound, then their questions |

**If you run long, cut in this order:** warm-up questions → the second design
question → Q9. **Never cut Q0, and never cut the PR review** — they are tasks 1
and 3 of the advertised round.

**If you run short:** D3 (classifier hand-off) expands to fill any remaining
time, and Q0's follow-up 3 (two reviewers, same case) opens into concurrency.

---

## 9. Verification record

Confirmed on 2026-09-16, from the repo root unless noted:

- `pytest -q` → **58 passed** (base), **64 passed** (with `PR.md` applied)
- `python wsgi.py` → starts on **port 5001**, first try
- `git apply --check --directory=mock-3 <patch>` → **applies cleanly**
- Every defect D1–D6 reproduced by a script; output pasted in §5
- Planted gap (§6) reproduced; **no test references `escalated`**
- App code: **1,634 lines across 12 files**, depth 1 below `app/`, 2 deps
- No candidate-facing README exists
