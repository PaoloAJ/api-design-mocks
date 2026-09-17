# Interviewer guide — mock-4, Shipping API

~60 min. First 20: background. Remaining ~40: three tasks, **~13 min each.**

**No code is written.** The candidate has no README, no search, and no IDE
index — they navigate by file tree and reading. Hand them the repo, `SPEC.md`,
and (at minute ~27) `PR.md`. Everything else comes out of your mouth.

Open with: *"This is a shipping API. Merchants create labels, carriers push
scan events, packages move through states, and a merchant can file a claim if
something goes wrong. Take three minutes, get oriented, then I'll ask you to
walk me through it."*

Start the app yourself if you want something live:
`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python wsgi.py`
→ `http://127.0.0.1:5001`, key `X-Ship-Key: ship-demo-key-acme`.

---

## Dataflow — the answer key for task 1

```
                  ┌─────────────── before_request chain ───────────────┐
  HTTP request →  │ assign_request_id → authenticate → enforce_rate_   │ → handler
                  │  (mint/echo)        (sets g.merchant_id, g.role)   │
                  │                      limit (keys on g.api_key)     │
                  └────────────────────────────────────────────────────┘
                                          ↓
   api/shipments.py            api/scans.py                  core/errors.py
   load_owned() → store.get    ingest_scans() → 202          every failure →
   → tenant check → 404        per-item _apply_one()         one JSON envelope
         ↓                            ↓
   services/shipping.py ←─────────────┘
   apply_transition()  ← both paths go through here
         ↓
   core/store.py  (dict behind RLock, version counter, cursor pagination)
```

**State machine** (`services/shipping.py:TRANSITIONS`):

```
  label_created ──→ in_transit ──→ out_for_delivery ──→ delivered ▣
        │                │                  │
        │                ├──→ exception ←───┤
        │                │        │
        └──→ cancelled ▣ │        └──→ in_transit / delivered
                         └──→ cancelled ▣          ▣ = terminal
```

**Ingest path (the async boundary):** `POST /v1/scans` takes up to 50 scans,
loops `_apply_one`, collects per-item errors with their `index`, returns **202**
— accepted, not "done". One bad row does not fail the batch.

**Two deliberate gaps.** Nothing notifies a merchant when a shipment hits
`exception`, and nothing reconciles a shipment that simply stops receiving
scans. Strong candidates ask where notification lives. Weak ones narrate it as
if it exists.

---

## Task 1 — the trace question ⚠️ NON-CUTTABLE

> **"A merchant creates a label for a package to Denver. Two days later UPS
> pushes a batch of 50 scans, one of which says that package was delivered.
> Walk me through everything that happens to that one scan, from the socket to
> the stored state."**

| Band | What it sounds like |
|---|---|
| **Weak** | Starts inside the handler. Doesn't mention middleware. Thinks the 202 means the work is done. Can't say where the state actually changes. |
| **Adequate** | Finds the `before_request` chain, `ingest_scans`, `_apply_one`, `apply_transition`, `store.update`. Explains the 202 correctly. |
| **Strong** | All of the above, plus: notes both the merchant and carrier paths funnel through `apply_transition` and says *why that matters*; notices the batch is not atomic and asks what happens on a partial failure; asks what tells the merchant. |

**Follow-ups, escalating:**

1. *"Scan 12 of 50 references a shipment that doesn't exist. What does the
   carrier get back?"* → 202, `errors[]` with `index: 11`, other 49 applied.
   Why index and not ID: so the carrier can retry exactly that row.
2. *"Why 202 and not 201?"* → We've accepted the batch; we're not promising
   downstream work finished. Probe: what *would* be async in a real version?
3. *"Same scan arrives twice — carrier retry. What happens?"* → Second is a
   no-op for state (`target != shipment["state"]` guard) but **writes a second
   scan row**. Scans aren't deduped. Good candidates spot this unprompted.
4. *"Rate limiting runs after auth. Why does the order matter?"* → Counter
   keys on `g.api_key`. Before auth you'd key on something spoofable, letting
   one caller drain another's budget.

**The planted gap — ask if they haven't found it:**

> *"A carrier scans `attempted` — a failed delivery attempt. What happens?"*

`SCAN_STATES["attempted"] = None` (`api/scans.py`). The scan is recorded, the
`if target` guard falls through, the shipment's state never changes, and the
202 reports the *old* state as if all is well. A package the carrier couldn't
deliver looks identical to one in transit. Reachable, untested, invisible
unless you read the dict against the branch.

---

## Task 2 — design questions

Pick two or three. Each has a concrete right-ish answer; the good ones hide a
coupling.

**1. "Add sorting — let merchants sort the list by weight."**
*The hidden coupling.* Cursor pagination encodes `(created_at, id)`. Change the
sort and every outstanding cursor is meaningless or, worse, silently wrong.
**Finding that coupling is the answer.** Follow-up: what do you do with a
cursor issued under a different sort? (Encode the sort in the cursor, reject
mismatches with 400 — `SPEC.md` rule 7 already says so.)

**2. "A merchant wants a webhook when a shipment is delivered."**
Where does it fire? Inside `apply_transition` (in the request path, so a slow
endpoint slows ingest) or from a queue (needs the transition to be recorded as
an event first). Push toward: transitions should emit events; delivery is a
consumer. Probe retries, ordering, and what happens if the webhook 500s.

**3. "Scans are arriving out of order — `delivered` before `out_for_delivery`."**
Right now the second scan 409s inside the batch. Options: buffer by scan
timestamp, make the state machine order-tolerant, or accept and reconcile.
No clean answer — you're watching them reason about a distributed ordering
problem, not recite one.

**4. "Ten million shipments. What breaks first?"**
`store.list()` materializes and sorts the whole collection on every list call.
Then: the process-local dict, the fixed-window limiter resetting per worker.
Strong answers name the cursor as the thing that *survives* the move to
Postgres, because it's an index seek.

---

## Task 3 — PR answer key

Hand over `PR.md`. Ask: **"What's off, and why does it matter?"**

The ranking is the signal. **A candidate who lists defects flat has reviewed a
diff; one who leads with D1 and says why it outranks the rest has done code
review.**

| # | Tier | Where | Defect | Spec rule |
|---|---|---|---|---|
| **D1** | **Deep / security** | `api/shipments.py` `load_owned` | `X-Ship-On-Behalf-Of` header used for tenant scoping. Any merchant reads any shipment. | **3** |
| D2 | Surface | `api/shipments.py` `get_shipment` | `queue_position` loads and sorts the whole collection to compute one integer. | **1** |
| D3 | Mid | `api/shipments.py` `list_shipments` | `destination_contains` filters *after* `paginate`. Short pages, wrong `has_more`. | **2** |
| D4 | Mid | `api/shipments.py` `bulk_cancel` | Writes `state` directly via `store.update`, bypassing `apply_transition`. Cancels a *delivered* shipment. | **5, 6** |
| D5 | Surface | `api/shipments.py` `bulk_cancel` | Another merchant's ID returns **403**, confirming it exists. | **4** |
| D6 | Mid | `services/shipping.py` | Duplicate-claim check lost its `status="open"` filter — a merchant can never file a second claim after the first resolves. | — |
| **D7** | **Deep / silent** | `api/scans.py` `_apply_one` | `delivered` scan skips `apply_transition` entirely. A `label_created` shipment jumps straight to `delivered` with a **202 and no error**. | **6** |

### Reproductions (all verified)

```
D1 tenant bypass via header:        200  Seattle, WA   ← another merchant's data
D2 queue_position present:          True -> 1
D3 asked limit=3, got 2 rows, has_more=True
D4 bulk-cancel revives delivered:   200  state=cancelled
D5 cross-tenant:                    403  forbidden     ← should be 404
D6 second claim after resolve:      409  duplicate_claim
D7 label_created -> delivered:      202  state=delivered  (skipped in_transit)
```

### What a strong candidate says

- **On D1:** "This is the one to fix before anything else. The comment says the
  proxy sets the header, but nothing here verifies that, and the app can't tell
  a proxy-set header from a client-set one. If it's ever reachable directly —
  or if the proxy doesn't strip it — any merchant reads any shipment by
  guessing IDs. That's a data breach, the rest are bugs." **Rule 3 forbids it
  outright.**
- **On D7:** "This one's worse than it looks because it returns 202. Nothing
  fails, nothing logs, the shipment just teleports. And it's the *reason a test
  changed* — the test that asserted illegal transitions are rejected got
  rewritten to assert the opposite. A changed test means a changed contract."
- **On D2:** "Detail endpoints shouldn't touch collections. Rule 1. At a
  thousand shipments per merchant this is a sort on every page load."
- **Noticing the changed test at all** is a strong signal. It's in the diff.

### False positives — do NOT credit these as defects

1. **`serialize()` looks like a pointless dict rebuild.** It isn't — it's the
   seam that keeps `merchant_id` off the wire. A candidate who wants it deleted
   has missed why it exists. *Good* candidates say "this looks redundant, but —"
   and then work out the reason. Credit the reasoning, not the flag.
2. **`with_etag(shipment, 201)` in `create_shipment` takes defaults elsewhere.**
   The defaults are correct.
3. **`bulk_cancel` has no pagination.** Looks like an oversight; it's a
   write endpoint taking an explicit ID list. Not a defect.

---

## Signals

**Strong**
- Reads `SPEC.md` before or during the PR task, and cites rule numbers.
- Asks how pagination works *before* designing sorting.
- Asks "what happens after the 202?" without being prompted.
- Ranks the PR defects by blast radius instead of by line number.
- Notices the changed test and treats it as a contract change.
- Says "I'd have to read X to be sure" instead of inventing behavior.

**Weak**
- Narrates the notification system that doesn't exist.
- Describes the handler without ever mentioning middleware.
- Lists all seven defects flat, security bug third, no severity reasoning.
- Wants to delete `serialize()`.
- Treats 202 as 201.
- Guesses at what a file does from its name without opening it.

---

## Timing

| Min | Phase |
|---|---|
| 0–20 | Background, their project, what they want |
| 20–23 | Orientation — they read, you stay quiet |
| 23–35 | **Task 1: trace question** + follow-ups |
| 35–47 | Task 2: design questions (pick 2) |
| 47–58 | Task 3: PR review |
| 58–60 | Their questions |

**Cut order when running long:**
1. Design question 3 or 4 (keep one of 1/2 — sorting is the best one).
2. PR defects D5 and D6 — take D1, D7, D2, D3 and stop.
3. Follow-ups 3 and 4 on the trace question.
4. **Never cut:** the trace question itself, or D1 in the PR.

---

## Verification record

- ✅ 57 tests green on the base; **58 green with `PR.md` applied** (the count
  in the PR matches).
- ✅ Every defect above reproduces via a script that was actually run — output
  pasted verbatim under "Reproductions".
- ✅ The planted `attempted` gap is reachable and untested.
- ✅ `PR.md`'s diff was generated from real edits and verified with
  `git apply --check` **from the repo root**.
- ✅ App code: 908 lines across 12 files under the §3 ceilings; depth 1.
- ✅ No candidate-facing README.
- ✅ Binds to 5001, not 5000.
