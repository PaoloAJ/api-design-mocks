# Interviewer Guide — REST API Design (Datadog), round 2

Companion to `README.md`. Use this after `mock-1` — the goal is to practice
the same skills (tracing dataflow, defending HTTP verbs and status codes,
naming failure modes) on a codebase you have *not* already memorized, since
the real interview drops you into one you've never seen.

The repo is an incidents/services API in Flask with an in-memory store.
About 700 lines of application code and 55 tests — small enough to read in
ten minutes, and it disagrees with `mock-1` on purpose in a few places so you
have to reason about *why*, not just pattern-match what you saw last time.

---

## 0. Format: this round is verbal, not a coding test

Same format as `mock-1` — see that file's §0 for the sourcing. In short: no
code gets written. You talk, and clear communication is graded as heavily as
correctness. The three phases (explore, design, review) each get roughly
equal time in the real thing, so budget your practice sessions accordingly
instead of spending all your time in one.

---

## 1. Dataflow summary

### Request lifecycle

Identical middleware chain to `mock-1` — same order, same reasoning:

```
HTTP request
  │
  ├─ before_request: assign_request_id
  ├─ before_request: authenticate          (/health and / skip this)
  ├─ before_request: enforce_rate_limit    (after auth, so it's keyed by identity)
  ├─ route handler
  │     validate body   -> 400 with every field error collected
  │     check ownership -> 404 if another org owns it
  │     check version   -> 409 if If-Match is stale
  │     read/write store, serialize
  ├─ errorhandler (if anything raised) -> one JSON envelope
  └─ after_request: attach_headers
```

If you did `mock-1` first: good, this should feel like nothing new. That's
the point — this layer is infrastructure, not domain logic, and recognizing
"I've seen this shape before" *quickly* so you can spend your attention on
what's actually different is a real skill, not a shortcut.

### Incident lifecycle — the interesting part

```
   triggered ──> acknowledged ──> investigating ──> monitoring
       │               │                │                │
       │               │                │                │
       └───────────────┴────────────────┴────────────────┘
                              │
                              ▼
                          resolved ──(reopen)──> triggered

   every edge above is legal; every OTHER edge is a 409.
   monitoring -> acknowledged is illegal (must pass through investigating,
   or resolve). acknowledged -> monitoring is illegal for the same reason.
```

Contrast with `mock-1`'s monitor status, which is any-state-to-any-state.
That's the single best "why does this codebase disagree with the last one"
question to ask a candidate who did both mocks: **why is one permissive and
the other strict?** There's a real answer — a monitor's status is a
*measurement* (the world just told you where things are, so any transition
is just "the truth changed"), while an incident's status is a *process*
people are actively running (skipping "investigating" on the way to
"monitoring" would mean claiming you're watching a fix that was never
identified). Strong candidates reach for "is this state observed or is it a
workflow?" as the general version of that rule.

Every transition — including the no-op case — is checked before anything
writes. A same-state call is a 200 no-op that writes nothing, same
convention as `mock-1`'s monitors, for the same reason: a flapping client or
retried webhook must not spam the timeline.

### Responders and the two flavors of idempotency

```
POST /v1/incidents               needs Idempotency-Key header to be retry-safe
POST /v1/incidents/{id}/responders   needs NOTHING -- it's idempotent for free
```

This is the single best "have they actually understood idempotency, not just
memorized the word" question in this repo. `POST /v1/incidents` creates a
*new* resource each time it's called with a fresh body — the operation has
no natural notion of "already done," so retry-safety has to be bolted on
with a key the client generates and the server remembers. `POST
.../responders` describes a *target state* ("alice is a responder") rather
than an action ("add one more responder"), so calling it twice converges to
the same state without any extra machinery. Push on this: "what would make
adding a responder need a key too?" — answer: if adding the *same* responder
twice was supposed to mean something (e.g., "bump their priority"), it would
stop being naturally idempotent and would need the same fix as create.

### Timeline — the audit trail mock-1 didn't have

`mock-1`'s own `INTERVIEWER.md` (Q20) flags "there is no audit trail of who
changed what" as a known gap. This repo's timeline is what filling that gap
looks like: an append-only log, one entry per meaningful event
(`created`, `status_change`, `responder_added`, `responder_removed`, `note`),
with **no PATCH or DELETE route for a single entry, anywhere**. If a
candidate proposes adding one, that's a good moment to ask what breaks — an
editable audit log isn't an audit log.

---

## 2. Questions to ask

Same grading philosophy as `mock-1`: the follow-up is where the signal is,
since nothing is written down and a rehearsed definition sounds identical to
real understanding for about one sentence.

### Q0 — The data-flow question (ask this one first, always)

**"An engineer notices `checkout` is erroring. They open an incident,
acknowledge it, add a teammate as a responder, post a couple of updates as
they dig in, and finally resolve it once the fix ships. Walk me through
every hop — what data moves, what shape it's in, and what the system does at
each step."**

A complete answer traverses:

```
POST /v1/incidents  {title, severity, service_id}
   -> validate, confirm service_id exists in this org
   -> create incident (status: triggered), write a "created" timeline entry
   -> 201, Location, ETag

POST /v1/incidents/{id}/status  {"status": "acknowledged"}
   -> check ALLOWED_TRANSITIONS, write "status_change" entry

POST /v1/incidents/{id}/responders  {"handle": "sam"}
   -> append if not already present, write "responder_added" entry

POST /v1/incidents/{id}/timeline  {"author": ..., "message": ...}  (x2)
   -> append-only note, no side effects on the incident itself

POST /v1/incidents/{id}/status  {"status": "investigating"} -> {"status": "resolved"}
   -> each hop checked against ALLOWED_TRANSITIONS
   -> resolving sets resolved_at, writes a final "status_change" entry
```

**Grading:**
- *Weak* — lists endpoints without connecting them, or thinks any status can
  follow any other (missing the strict FSM).
- *Adequate* — traces the sequence correctly and knows which call writes to
  the timeline.
- *Strong* — notices that **every** state-changing call writes exactly one
  timeline entry except the naturally-idempotent responder add-when-already-
  present (which writes zero, on purpose), and can say why that's consistent
  rather than an inconsistency.

**Follow-ups:**
1. "What happens if the acknowledge call times out and the client retries
   it?" — safe: same-state transitions are a no-op, no duplicate entry.
2. "What if the *create* call times out and retries?" — unsafe *without* an
   Idempotency-Key: two incidents. This is the fork from Q0's mock-1
   equivalent — make them say which calls in this flow need the header and
   which don't, and why.
3. "The engineer's teammate is in a different org's account by mistake and
   tries to acknowledge it." — 404, not 403, and not "it worked because
   they're both logged in" — tenant isolation is per-request, not per-user.

### Warm-up — orienting in the system

**Q1. Why is there no `DELETE /v1/incidents/{id}`?**
An incident is an audit record of something that happened; deleting it
destroys history a postmortem or a compliance review might need later.
Contrast with `mock-1`, which *does* allow deleting a monitor (a monitor is
current configuration, not history). Strong answer: distinguishes "current
state" resources (deletable) from "record of an event" resources (usually
not), and notes you'd want a `resolved`+time-based archival policy instead
of deletion if storage became a concern.

**Q2. Why does adding a responder use its own endpoint instead of `PATCH
{"responders": [...]}`?**
`PATCH` with a full array forces the client to read-modify-write and race
against anyone else doing the same; a client that fetches, appends locally,
and PATCHes back can silently lose a concurrent addition. `POST
.../responders {"handle": "x"}` is a server-side set-union — no read needed
first, no lost update possible.

**Q3. Why is `severity` neither settable on create-and-forget nor patchable?**
It *is* settable on create (required field) — it's specifically
*un-changeable after creation* that's the gap. That's deliberate: see
`pr_review/` for the PR that adds a way to change it, and grade how the
candidate reviews the approach it takes.

**Q4. Walk me through `GET /v1/incidents?tag=env:prod&severity=sev1`.**
Middleware chain, then filter (status/severity/service/tags/q, all AND-ed),
then paginate, then serialize — filtering before paginating, same reasoning
as `mock-1` Q1.

### Core — design judgment

**Q5. The timeline's `author` field is just a string the client sends. What's
wrong with that?**
Nothing stops a client from claiming to be someone else — `{"author":
"priya"}` in a note POST isn't verified against the API key at all (there's
only one API key per org here, not per user). A real system would derive
`author` from an authenticated user identity, never trust a body field for
it. Good follow-up: "does this matter for `status_change` and
`responder_added` entries too?" — those already hardcode `"system"`, so
they're not vulnerable to this, which is itself worth noticing.

**Q6. `POST .../responders` returns `200`, not `201`. Defend that.**
`201` implies something new was created at a location; there's no
`/v1/incidents/{id}/responders/{handle}` resource with its own identity
being returned (well — there sort of is, for `DELETE` purposes, but the
response body is the whole incident, not a "responder" resource). `200` with
the updated parent is consistent with `mock-1`'s "PATCH-like" mutations.
Reasonable candidates disagree here — the point is a defended choice, not
this one.

**Q7. Why does `POST /v1/incidents/{id}/timeline` allow notes on a resolved
incident, but `POST .../status` refuses most transitions out of `resolved`?**
Because they're different kinds of writes. Status describes *where the
incident is*, and "resolved" should mean something stable. A timeline note
describes *what happened*, and writing the postmortem happens **after**
resolution — the append-only log has to stay open past the point the
workflow closes, or you couldn't document the incident at all.

**Q8. `find_one` in `create_incident` checks the service exists, then
creates the incident. Name the race.**
Two concurrent requests can both pass the existence check right before the
service is (hypothetically) deleted — not exploitable today since services
have no delete route, but it's the same shape of bug as `mock-1`'s duplicate-
name race (Q14 there), and worth naming even though nothing currently
triggers it: the *pattern* (check-then-act without a constraint enforcing
it) is the bug, not today's specific consequence.

**Q9. Where would you put a "time to acknowledge" SLA, and how would you
compute it?**
`created_at` on the incident and the first `status_change` timeline entry
with `message` containing "acknowledged" — or, better, that's a sign the
timeline entries should have a structured `to_status` field instead of a
free-text message, so downstream code isn't parsing sentences. Good
candidates notice the message field is human-readable prose and flag that as
a modeling smell once you ask them to compute something from it.

### Depth — scale and operations

**Q10. 10,000 incidents, and `GET /v1/incidents?tag=env:prod` is slow. Fix
it.**
Same shape as `mock-1` Q21: push filtering into an index (or a database) so
it isn't a Python scan of every record on every request; a tag is a natural
secondary index (`tag -> [incident_id]`) if this were persisted.

**Q11. Replace the dict with Postgres. What changes?**
`responders` stops being a plain list column if you ever need to query "all
incidents alice is on" efficiently — that becomes a join table. `severity`
gaining a write path (see `pr_review/`) would need to go through the same
optimistic-lock pattern as everything else, which is exactly what the PR
under review fails to do.

**Q12. How do you make this observable?** (Expect fluency — it's Datadog.)
Same gaps as `mock-1` Q18: RED metrics per route, status/error-code
dimensions, distributed tracing instead of a bare request-id, structured
logs. Domain-specific addition: **time-in-state** as a histogram (how long
do incidents spend "acknowledged" before moving?) is exactly the kind of
metric this API's own data enables and doesn't currently emit.

### Curveballs

**Q13. Design incident merging: two open incidents turn out to be the same
underlying issue. Merge B into A.**
Wants: which fields win (severity — probably the more severe of the two);
responders — union; timeline — concatenate in time order, with an entry
marking the merge itself ("merged from inc_B") so the audit trail explains
the jump; what happens to B — not deleted (no delete route, on purpose), so
it needs a `merged_into` pointer and probably a `merged` status that isn't
in today's `STATUSES` set at all. Strong candidates notice the state machine
itself has to grow, not just the endpoint.

**Q14. Same idempotency key, different request body on `POST /v1/incidents`.
What should happen?**
Same answer as `mock-1`'s equivalent (its §5, exercise 4): today's code
would silently return the original resource; the right behavior is `422`
because the client has a bug, and a body fingerprint needs to be stored
alongside the key to detect the mismatch.

**Q15. You're on call. Incident *creation* rate just went to zero for the
last 20 minutes, org-wide. Go.**
Not a design question — a debugging one. Distinguish "nobody has had an
incident in 20 minutes" (fine) from "the create endpoint is broken and
nobody can page anyone" (very not fine) — which requires knowing your
baseline creation rate, which requires the RED metrics from Q12 already
existing. Candidates who jump straight to reading code without asking "what
changed, and what's the normal rate" are showing you how they'd behave
paged at 3am.

---

## 3. What to look for

Same signals as `mock-1` §3 — repeated here only for the ones sharpened by
this repo specifically:

**Strong signals**
- Notices the strict-vs-permissive state machine difference *unprompted* and
  can articulate the "observed value vs. active workflow" distinction.
- Distinguishes the two idempotency mechanisms and says why each fits its
  endpoint.
- Asks "who's the author, really?" about the timeline before being pushed.
- Treats the missing `DELETE` and missing `severity`-write as *deliberate*
  design questions to interrogate, not oversights to silently accept.

**Weak signals**
- Assumes this codebase's decisions are "the same as last time" without
  checking — misses that transitions are restricted here.
- Can't explain why `responders` doesn't need an idempotency key when create
  does.
- Doesn't ask what happens to timeline history when proposing a delete or
  merge feature.

## 4. Suggested 45-minute structure

Same shape as `mock-1` §4. Q0 first, always, 8-10 minutes. Then 2-3 warm-ups,
3-4 core questions, 1-2 depth questions, one curveball if time remains, then
their questions.

## 5. Verbal design exercises

No code is written — these are "talk me through it." Each is 5-10 minutes.

1. **"Design incident merging."** (Q13 above, expanded.) Push specifically on
   what happens to `resolved_at`, `service_id` if they differ, and whether
   merging is itself a state-machine transition or a separate action outside
   it.

2. **"Add an SLA: page someone if a sev1 isn't acknowledged within 5
   minutes."** Wants: this can't live in the request/response cycle (nothing
   is polling); needs a background job or a delayed-message queue keyed off
   `created_at`; the job needs to be cancelled/no-op if the incident *is*
   acknowledged in time (a naive "sleep 5 minutes then check" race — what if
   it checks half a second before the acknowledge lands?); and where does the
   page itself go (another async, retryable hop, same shape as `mock-1`'s
   webhook-notification exercise).

3. **"Design `POST /v1/incidents/{id}/postmortem` — generate a shareable
   summary doc from the timeline."** Forces: is the output stored or
   generated on demand each time; if stored, what invalidates it if new
   timeline entries land after generation; what format (structured JSON an
   internal tool renders, vs. already-rendered Markdown) and who's the
   consumer.

4. **"Same idempotency key, different body."** (Q14 above.)

5. **"You're on call, incident creation went to zero."** (Q15 above — good
   closer, since it's a debugging reflex question rather than a design one.)

---

## 6. Prep notes for the candidate side

1. **Deliver Q0's trace cold, in under three minutes**, without looking at
   the diagram above.
2. **One sentence each for:** why the status machine here is strict but
   `mock-1`'s isn't; why responders don't need an `Idempotency-Key` but
   create does; why there's no `DELETE` on an incident; what's missing from
   `author` on a timeline note.
3. **Do the PR review in `pr_review/` cold before reading its answer key.**
   That phase is graded as heavily as the other two in the real interview
   and gets the least natural practice, since most self-study is
   design-question flashcards. Spot as many of the planted issues as you can
   in 10 minutes, out loud, before checking your list against the key.
