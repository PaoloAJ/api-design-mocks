# Interviewer guide — mock-7, Delivery API

Transactional email: a send is accepted and queued, a worker reports what the
provider did, the message ends delivered or bounced.

**Candidate gets:** the `mock-7/` tree, `SPEC.md`, and `PR.md` at minute 27.
**Candidate does not get:** a README, search, or grep. They read files.
**No code is written.** Everything is verbal.

| Phase | Time | What |
|---|---|---|
| Background | 0–20 | Their project, what they want from the internship |
| 1. Explore | 20–33 | Orient, then trace the data flow |
| 2. Design | 33–46 | Add a feature, out loud |
| 3. Review | 46–59 | `PR.md` — what's off and why it matters |

Setup: `cd mock-7 && python3 -m venv .venv && source .venv/bin/activate`,
`pip install -r requirements.txt`, `python wsgi.py` → port **5001**.
`pytest -q` → **57 passed**.

---

## 2. Dataflow — the answer key for task 1

```
POST /v1/messages
  │
  ├─ before_request: assign_request_id()   X-Request-ID, or mint one
  ├─ before_request: authenticate()        Bearer key -> g.account   (401)
  ├─ before_request: enforce_rate_limit()  keyed on g.account["id"]  (429)
  │
  ├─ validate_message()                    collects ALL field errors (400)
  ├─ store.get("templates", ..., account_id=)   unknown/other tenant -> 404
  ├─ Idempotency-Key? -> replay original, 202 + Idempotent-Replay: true
  ├─ store.create("messages")              status=queued, version=1
  └─ 202 + ETag + Location            <<< ASYNC BOUNDARY: nothing sent yet
                │
                │   (a worker, outside this codebase, attempts delivery)
                ▼
POST /v1/messages/{id}/transition        worker reports the outcome
  ├─ validate_transition() vs ALLOWED_TRANSITIONS   illegal -> 409
  ├─ If-Match present? -> version check             stale  -> 409
  ├─ side effects by target:
  │     sending   -> attempts += 1
  │     delivered -> last_error = None
  │     bounced   -> requires `reason`, stores it
  │     deferred  -> ** NOTHING **   (see the planted gap below)
  └─ terminal? -> store.create("events", ...)

POST /v1/events        provider callbacks, batched
  └─ per item: validate -> find_one(scoped) -> create event
     202 {accepted, rejected, results[], errors[{index, code, message}]}
```

**State machine**

```
queued ──> sending ──> delivered   (terminal)
              │  ▲──┐
              │     │
              ├──> deferred ──┘     (soft failure, retried)
              └──> bounced          (terminal, needs a reason)
```

**Two deliberate gaps.** Strong candidates ask about these; weak ones narrate
them as if they exist.

1. **Nothing sends mail.** No SMTP client, no worker, no queue. The service
   only records what someone else claims happened.
2. **Ingesting an event does not move the message.** `POST /v1/events` writes
   an event row; the message's status is untouched unless a worker separately
   calls `/transition`. The two paths never meet.

---

## 3. The trace question — NON-CUTTABLE

Ask this one verbatim. It is what the round exists to ask.

> A customer's server calls `POST /v1/messages` to send a welcome email to
> `ada@example.com`. Walk me through every piece of code that request touches,
> from the moment it arrives to the moment the customer sees a response. Then
> tell me: at what point has the email actually been sent?

| Band | What it sounds like |
|---|---|
| **Weak** | Goes straight to the handler. Misses middleware entirely. Says the email is sent when the endpoint returns 200. Cannot say where `g.account` comes from. |
| **Adequate** | Finds the three `before_request` hooks in order, follows `send_message`, names validation and the store write, notices the `202`. |
| **Strong** | All of the above, *and*: explains why `202` rather than `201`, identifies that nothing in this codebase actually sends mail, and points at `/transition` as where the outcome comes back. Asks who calls it. |

**Follow-ups, escalating:**

1. *Why is `authenticate` registered before `enforce_rate_limit`?* → the
   counter keys on `g.account["id"]`; on IP, one customer behind a NAT could
   exhaust another's budget, and an attacker just rotates IPs.
2. *The same request arrives twice — the client timed out and retried. What
   happens?* → `Idempotency-Key` replays the original. Then push: what if the
   key is reused with a *different* body? (No fingerprint — returns the first
   message. Real gap, in a comment in `store.py`.)
3. *A customer asks for a message that belongs to a different account. What do
   they get, and why not `403`?* → `404`. `403` confirms the ID exists, which
   turns the endpoint into an enumeration oracle.
4. *Where could a message get stuck forever?* → the `deferred` gap. Best
   question in the set; almost nobody gets it unhinted.

---

## 4. Design questions — task 2

### Primary: delivery-rate limits per recipient domain

> We're getting complaints that we blast too many messages at one mail
> provider at once and get throttled. Design a per-domain send rate limit.

**This is a rule for all future sends, not a cleanup of what's queued.** The
candidate may read it as "fix the backlog." That misreading is expected and is
a real signal — let them run with it for a moment, then say, verbatim:

> "To be clear — this should apply to every message we accept from now on, not
> just the ones already in the queue."

*Free hint* (costs nothing): restating that scope.
*Costs credit*: naming the coupling below for them.

**The hidden coupling:** a per-domain limit needs a count of recent sends per
domain. There is no index on recipient domain, and `store.list()` is the only
read — so the naive design scans the collection on every send, which is
exactly what SPEC rule 1 prohibits and what the PR gets wrong. Strong
candidates propose a counter keyed by domain, maintained on write.

Also good: *where* does the limit live — reject with `429` at send time, or
accept and delay inside the async boundary? The `202` contract makes the
second one legal, which is the more interesting answer.

### Secondary questions

| Question | The answer that matters |
|---|---|
| Add sorting (`?sort=updated_at`) to `GET /v1/messages`. | Breaks cursor pagination: the cursor encodes `(created_at, id)`. SPEC rule 5 — `updated_at` is mutable, so a paging walk can repeat or skip rows. Finding that coupling *is* the answer. |
| Add a `POST /v1/messages/{id}/resend`. | Why a sub-resource, not `PATCH status=queued`: it has side effects and the transition table forbids leaving a terminal state. Should it be idempotent? |
| Webhooks to notify customers of bounces. | Gap 2. At-least-once delivery, retries, and why the customer's endpoint must be idempotent. Careful: this is where candidates start larping about queues — steer back to the API contract. |

---

## 5. PR answer key

Hand over `PR.md` at ~minute 46. **Six defects, three false positives.**

**The standard nudge**, if they stall: *"have another look at the spec's
constraints."*

Every defect below was reproduced by `repro_pr.py` (apply the PR first).
Verified output is in §6.

| # | Tier | File | Defect | SPEC |
|---|---|---|---|---|
| **D1** | **Security** | `messages.py` `list_messages` | `X-Delivery-Account` header overrides tenant scope | **3** |
| **D2** | Security | `events.py` `ingest_events` | `find_one` lost `account_id=` — cross-tenant write | **4** |
| **D3** | **Deep** | `messages.py` `send_message` | Suppression returns `200` with no message; mail silently dropped | shape |
| **D4** | Mid | `messages.py` `get_message` | Loads whole collection to compute `recipient_history` | **1** |
| **D5** | Mid | `events.py` `list_events` | Filters *after* paginating | **2** |
| **D6** | Surface | `middleware.py` | Logs the `Authorization` header in plaintext | **7** |

### D1 — lead with this one

Four lines. The comment claims "our internal proxy strips this header," which
is exactly the kind of assumption that is false in practice and unverifiable
from the code. Any customer sets `X-Delivery-Account: acct_globex` and reads
another tenant's mail.

> **A candidate who lists six defects flat has reviewed a diff. One who leads
> with D1, says it outranks the others because it is a live data breach rather
> than a latency problem, and notes the comment is an unverifiable claim — has
> done code review.** That is the discriminator for this case.

Ask: *"the comment says the proxy strips it — does that make it safe?"*
(No. Defense in depth; the header is trusted input either way, and SPEC 3 says
scope comes from `g`, full stop.)

### D2

Note the comment directly above still says "scoped to the account" while the
code no longer is. Comment-code contradictions are free evidence. Compounds
with D3: a forged bounce event poisons the suppression list, letting one
tenant block another's mail.

### D3 — the deep one

`200` with `{"status": "suppressed"}`. Every other path returns `202` with an
id, ETag and Location. A client that stores `response["id"]` gets a KeyError;
one that checks only the status code believes the mail was queued. Nothing is
recorded, so the message is invisible in `GET /v1/messages` — support cannot
tell a suppressed send from one that never arrived. Suppression may well be
the right *feature*; this is the wrong *contract* for it.

Strong answer: return `202` with a real record in a `suppressed` state, or
`409` with the error envelope. Either way it must be visible and must not
masquerade as a shape the client cannot parse.

### D4 — the mandatory full scan

The docstring two lines above literally says *"This handler must never load
the collection — see SPEC rule 1."* The diff adds `store.list()` directly
beneath it. Same defect in the send path (D3's suppression loop), which makes
every send O(collection).

### D5

The PR description calls this an optimization — "one pass over the page
instead of scanning every record." It is the opposite: correctness is gone.
The filter now runs on 20 already-sliced rows, so a matching row on page 2 is
reported as not existing. Watch for candidates who read the description and
accept the framing without checking.

### D6

Everyone should find this. A candidate who misses it did not read the diff.

### False positives — do not let them count these

| Looks wrong | Actually fine |
|---|---|
| `item.get("detail", None)` — redundant default | Identical to `.get("detail")`. Harmless, arguably clearer. A candidate who calls this a *bug* has not distinguished style from defect. |
| Test renamed `test_ingest_rejects_...` → `test_ingest_accepts_...` | The rename is *correct* for the new behavior. The defect is D2, the behavior change it documents — not the rename. **A changed test means a changed contract: the right move is to ask why, and follow it to D2.** Credit the candidate who uses it as a thread, not the one who flags the rename itself. |
| `paginate(records, limit=limit, cursor=cursor)` taking defaults | Defaults are correct (20, max 100). Nothing wrong here. |

---

## 6. Verified reproduction

`source .venv/bin/activate && python repro_pr.py` with the PR applied:

```
D1  SECURITY: X-Delivery-Account forges tenant scope (SPEC 3)
acme sees own messages:        2
acme + forged header sees:     1  -> ['finance@globex.test']

D2  SECURITY: ingest lost account scoping (SPEC 4)
globex posting an event on acme's message: accepted=1

D3  DEEP: suppression silently drops mail, returns 200
resend after bounce -> HTTP 200  body={'status': 'suppressed', 'to': 'repeat@acme.test'}

D4  FULL SCAN: GET /messages/{id} loads the whole collection (SPEC 1)
store.list() calls for ONE keyed GET: 1

D5  CORRECTNESS: events filtered after pagination (SPEC 2)
events for target message: 0   (correct answer: 1)
has_more=True

D6  SECURITY: API key written to the request log (SPEC 7)
GET /v1/messages -> 200 in 0.06ms (request_id=f679... auth=Bearer key_acme)
```

**The planted logic gap** (in the clean codebase, not the PR) —
`deferred` is a legal transition target that hits no branch in
`transition_message`, and no test covers it:

```
after sending:  status=sending   attempts=1
after deferred: status=deferred  attempts=1   (HTTP 200)
retry sending:  status=sending   attempts=2
events recorded for this message: 0
```

Status changes, but no attempt is counted on the deferred leg, no event is
written, and `last_error` is never set — so a message can loop
`sending → deferred → sending` forever and the only counter that could stop it
is the one that path never touches. Reachable, untested, invisible unless you
read `STATUSES` against the branches in the handler.

---

## 7. Signals

**Strong**
- Opens `app/__init__.py` first to find the routes, not a random handler.
- Asks "what calls `/transition`?" — finds the async boundary unprompted.
- Reads `SPEC.md` before reviewing, and cites rule numbers.
- Leads the review with D1 and ranks by blast radius, not by file order.
- Notices the comment in D2 contradicts the code beneath it.
- Treats the changed test as a thread to pull, not a nit to flag.
- Says "I'd check X" instead of asserting something they haven't read.

**Weak**
- Narrates the two gaps as if implemented ("then it sends the email").
- Lists defects in diff order with no severity ranking.
- Flags the `.get("detail", None)` default as a bug.
- Accepts the PR description's "optimization" framing for D5.
- Redesigns the storage layer when asked about rate limits (larping).
- Cannot say where `g.account` is set after tracing a request.

---

## 8. Timing and cut order

| Minute | Item |
|---|---|
| 0–20 | Background |
| 20–24 | Orientation — let them read. Do not narrate the tree. |
| 24–33 | **Trace question** + follow-ups 1–2 |
| 33–46 | Design: primary, then one secondary |
| 46–59 | `PR.md` |
| 59–60 | Their questions |

**Cut in this order when short:**

1. Secondary design questions (keep the primary).
2. Trace follow-ups 3–4.
3. D6 and the false positives — if they found D1 and D3, you have your signal.

**Never cut:** the trace question, and D1 in the review. Those two carry the
round.
