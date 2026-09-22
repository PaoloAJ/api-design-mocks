# Datadog SWE Intern Mock: Answer Key

Everything below that says *verified* was checked by actually calling the app, so you can trust the behavior described.

**Contents**
1. [Phase 1: Exploring the codebase](#phase-1-exploring-the-codebase)
2. [Phase 2: Feature design (transfers)](#phase-2-feature-design-transfers)
3. [Phase 3: PR review](#phase-3-pr-review)
4. [Communication playbook and your questions for them](#communication-playbook)

---

## Phase 1: Exploring the codebase

### The 20-minute plan (say each step out loud)

| Minute | What you do | What you say |
|---|---|---|
| 0–2 | Look at the **file tree** only. | "Small Flask service with about 6 Python files. Folders are `api`, `services`, `storage`, `models`, plus CSV seed data. No README or tests. Looks like a layered design, so I'll start at the entry point." |
| 2–5 | Open `app.py`. **Read the imports first.** | "The imports are basically the architecture: two blueprints, a `Ledger` service, a `CsvStore`. `create_app` builds the store, wraps it in a Ledger, puts it in `app.config`, registers routes and one error handler." |
| 5–13 | **Trace one POST:** `POST /accounts/<id>/transactions`. | Narrate each hop (see the diagram below). |
| 13–16 | Skim the **other endpoints by signature only** and compare them to the one you traced. | "`list_accounts` goes straight to `store`, skipping the service layer. That's inconsistent." |
| 16–18 | Open `data/*.csv`. Compute one balance by hand. | "acc_0001 is 2500 − 120.45 + 2500 = 4879.55. There are frozen and closed accounts in the seed data, so status matters." |
| 18–20 | **Summarize:** purpose, layers, data flow, top 3 weak spots, open questions. | "Here's my mental model…" |

**Don't:** read `models.py` top to bottom first, or spend 5 minutes on one function. **Do:** say what you're skipping and why. "I'm going to skip `list_accounts` for now since GET is simpler. I'll trace the POST because it touches validation, state, and storage."

### The mental model (data flow)

```
HTTP POST /accounts/acc_0001/transactions  {"type":"withdrawal","amount":"20.00"}
   │
   ▼
app.py  create_app()  ── registered transactions_bp, errorhandler(LedgerError)
   │
   ▼
api/transactions.py  create_transaction(account_id)
   │  request.get_json(silent=True) → None? → 400 "request body must be JSON"
   │  ledger() = current_app.config["LEDGER"]
   ▼
services/ledger.py  Ledger.record_transaction(account_id, payload)
   │  get_account()        → store.get_account()  → None? → NotFoundError   404
   │  status != "active"                                   → ConflictError   409
   │  type not in TRANSACTION_TYPES                         → LedgerError     400
   │  parse_amount()  (not number/NaN, ≤0, >1,000,000, >2dp) → LedgerError     400
   │  withdrawal and amount > balance()                     → UnprocessableError 422
   │      balance() = Σ deposits − Σ withdrawals via store.transactions_for()
   │  Transaction(id=store.next_transaction_id(), ...)
   ▼
storage/csv_store.py  add_transaction() → self.transactions.append(...)   (memory only)
   │
   ▼  back up
api/transactions.py  jsonify(transaction.to_dict()), 201     (amount serialized as string)

Any LedgerError anywhere → app.py handle_ledger_error → {"error": msg}, error.status_code
```

### Questions you should ask the interviewer *while* exploring

Asking good questions is part of the grade. Ask them as you find things, not all at the end.

| Question | Why it's a good one |
|---|---|
| "Who calls this service? A mobile app, other internal services?" | Tells you how much to trust input and what "breaking the API" means. |
| "Is the CSV the source of truth, or just seed data? Should writes persist?" | Writes are never saved back. Is that a bug or on purpose? |
| "How is this deployed: one process, or multiple workers?" | In-memory state is only correct with a single process. |
| "Is the Flask server threaded in prod?" | Decides whether the check-then-write race is real. |
| "Is balance *intentionally* derived from transactions instead of stored?" | Shows you spotted a design choice and aren't assuming it's wrong. |
| "Is there auth in front of this, like a gateway?" | Nothing here checks who is calling. |
| "Is there a test suite somewhere else? How does the team usually run this?" | There are no tests in the repo. `create_app(data_dir)` looks built for tests. |
| "Is 422 for insufficient funds vs 409 for frozen a team convention?" | Shows you're thinking about API contracts. |
| "Why does `list_accounts` go straight to the store?" | Asks about an inconsistency without assuming it's a mistake. |
| "About how many transactions per account are we expecting?" | `balance()` scans every transaction on every call. |

How to phrase them: **state what you see, give your guess, then ask.** For example: "I see writes only go to the in-memory list and never back to the CSV. I'm guessing that's fine for this exercise, but is it intended?"

### Answers to the Phase 1 questions

**1. What does it do?**
It's a small REST API for a bank ledger. You can open accounts (checking, savings, or brokerage, each with a currency and status), record deposits and withdrawals, and read balances and transaction history. Data is seeded from CSV into memory at startup.

**2. How did you orient yourself?**
File tree → entry point (`app.py`) → imports as a map of the architecture → trace one POST request through every layer → skim the rest by function signatures → look at seed data → summarize. I went top-down on purpose: build the map first, then go deep on one path. That beats reading line by line.

**3. Job of each folder**
- `app.py` is the **composition root** (app factory). It wires the store → ledger → blueprints and registers the error handler. It also does dependency injection through `app.config["LEDGER"]`.
- `api/` is the **HTTP layer**. It parses requests, calls the service, picks the success status code, and serializes the response.
- `services/` holds the **business rules**: validation, account status checks, balance math, insufficient funds, and the error types that carry status codes.
- `storage/` is **persistence**. It loads the CSVs into a dict and a list, generates IDs, and does lookups.
- `models/` holds **data shapes and enums**: dataclasses plus allowed types and statuses.
- `data/` is the **seed data**.

Why split it this way: separation of concerns. You could swap `CsvStore` for Postgres without touching `api/`. And `create_app(data_dir=...)` lets tests load their own fixture data.

**4. Trace a request:** see the diagram above.

**5. Where is data stored? What happens on restart?**
`CsvStore._load()` reads `data/accounts.csv` and `data/transactions.csv` **once** at startup into `self.accounts` (a dict keyed by id) and `self.transactions` (a list). Writes only change memory and are **never written back to the CSV**. So a deposit is **lost on restart**. Two related problems:
- With multiple worker processes, each one has its **own copy**, so reads disagree between workers.
- If a CSV file is missing, `_read` quietly returns `[]`, so the app starts empty with no warning. That hides config mistakes.

**6. Where does balance come from?**
It's **derived**, not stored: `Ledger.balance()` sums deposits and subtracts withdrawals. That's ledger or event-sourcing style.
- **Pros:** one source of truth, a built-in audit trail, and the balance can't drift away from the transactions.
- **Cons:** it's O(total transactions) on every call. `transactions_for` scans the *whole* list, not just this account, and every withdrawal calls it. Also, any transaction type other than deposit or withdrawal is **quietly ignored** (keep this in mind for the PR).
- **Fix at scale:** store a running balance, updated inside the same write as the transaction, or keep periodic snapshots.

**7. ID generation**
`f"acc_{len(self.accounts) + 1:04d}"`, which is length + 1. It breaks when:
- anything is ever deleted, or the CSV ids have gaps (you get a duplicate id);
- two requests run at the same time (both read the same length and get the same id).

For accounts this is dangerous: `add_account` does `self.accounts[account.id] = account`, so a duplicate id **silently overwrites an existing customer's account**. Fixes: `uuid4`, or a counter protected by a lock and seeded from the max existing id.

**8. Error → HTTP response**
Services raise `LedgerError` subclasses, and each one carries a class-level `status_code`. The views don't catch them, so they bubble up to `@app.errorhandler(LedgerError)` in `create_app`, which returns `{"error": message}` with that status. **Gap:** any *other* exception, like an `AttributeError`, becomes Flask's default **HTML** 500. That breaks the JSON error format.

**9. Status codes**

| Code | When | Opinion |
|---|---|---|
| 400 | Bad input: missing owner, bad type or currency, bad amount, body isn't JSON | Good |
| 404 | Account id not found | Good |
| 409 | Account is frozen or closed | Reasonable: the request conflicts with the resource's current state |
| 422 | Insufficient funds | Reasonable: the request is well-formed but can't be processed. Some teams use 409 or 402 instead. **Consistency and documentation matter more than which one you pick.** |
| 500 | Unexpected input types (see Q10) | Bug. These should be 400s. |

Also missing: a JSON catch-all 500 handler. And status is checked *before* the input is validated, so a garbage body sent to a frozen account gets 409 instead of 400. That's debatable, but worth mentioning.

**10. Edge cases (all verified)**
- a) `"NaN"`: `Decimal("NaN")` parses, but `is_finite()` catches it → **400** "amount must be a number".
- b) `"10.005"`: exponent −3 → **400** "cannot have more than 2 decimal places".
- c) Withdraw exactly `750.00` from a 750.00 balance: the check is `amount > balance`, so it's **allowed, 201**, and the balance becomes 0.00. That's the correct boundary.
- d) JSON list body: `get_json` returns a list, which isn't `None`, so it gets past the check. Then `payload.get` raises `AttributeError` → **500** (HTML). `POST /accounts` has the same bug.
- e) `"description": 42`: validation passes, then `(42 or "").strip()` raises → **500**. Nothing is saved, because this happens while the `Transaction` is being built, before `add_transaction`. `"owner": 123` on `POST /accounts` is also a 500.
- f) `acc_0004` is frozen → **409** "account acc_0004 is frozen".

**11. `?type=bogus`**
Returns **200 with `count: 0`**. That's a quiet failure: a client typo looks like "no transactions." Better to return 400 and list the valid types.

**12. Inconsistencies**
- `list_accounts` calls `ledger().store.list_accounts()` directly, **bypassing the service layer**.
- A bad body on `POST /accounts` becomes `{}` and returns "owner is required", while `POST .../transactions` returns "request body must be JSON".
- The `ledger()` helper is copy-pasted into both blueprints.
- `GET /accounts/<id>` looks up the account twice, since `balance()` calls `get_account` again.
- The account list doesn't include balances, but the single-account GET does.
- `?owner=` filters on a display name. Names aren't unique, and it isn't authorization.

**13. Top 3 production worries**
1. **Durability and multi-process state.** It's a bank ledger held only in memory: data is lost on restart, and workers disagree with each other.
2. **Concurrency.** Check-then-write with no lock leads to overdrafts and duplicate IDs that can overwrite accounts.
3. **No authentication or authorization.** Anyone can withdraw from any account id.

Honorable mentions: 500s on wrong input types, O(n) scans, no pagination on lists or history, no observability.

**14. Two concurrent withdrawals**
acc_0001 has 4879.55. Two requests for 3000.00 arrive at once. Both call `balance()` → 4879.55, both pass `amount > balance`, and both append. The balance ends up at **−1120.45**. Flask's dev server is threaded by default, so this can really happen. Both requests could also get the same `txn_` id. **Fix:** a lock around check-and-write in-process. With a real database, use a transaction with a row lock (`SELECT ... FOR UPDATE`) or a conditional update.

**15. End-to-end tests (first 5)**
Use `create_app(data_dir=tmp_fixture)` so each test starts from known data, then call it through `app.test_client()`.
1. Deposit → 201, the balance goes up, and it shows up in history.
2. Withdraw the exact balance → 201 and balance 0.00. Withdraw balance + 0.01 → 422.
3. Frozen or closed account → 409. Unknown account → 404.
4. Table-driven bad amounts: `0`, `-1`, `"abc"`, `"NaN"`, `"1.001"`, `1000000.01`, `null` → all 400.
5. Non-object body or non-string description → **should** be 400. This test would *fail* today and expose the 500 bug.

**16. Observability (Datadog angle)**
- **APM tracing** on Flask (ddtrace auto-instruments it): latency and error rate per endpoint and status code.
- **Metrics:** transactions created by type, insufficient-funds count, 4xx/5xx rates, number of accounts and transactions in memory (memory growth).
- **Structured logs** with `account_id`, `type`, `status_code`, and a request id. Never log sensitive data.
- **Monitors:** 5xx spike, p99 latency (the O(n) scan gets slower as data grows), and an invariant check like "no negative balances."
- `/health` is shallow. It says "ok" even if the data failed to load.

**17. Questions you wanted to ask:** see the table above.

**18. Asking for help (script)**
> "I've spent about 10 minutes on how X works. I've looked at A and B, and my best guess is C. The part I can't confirm is D. Could you tell me if I'm on the right track, or point me to where D happens?"

Show what you already tried, give your guess, and ask a *specific* question. Then **use the hint and say how it changed your thinking.**

**19. What kind of engineer are you?**
> "I go top-down. I build a map of the system first, then pick one real request and trace it end to end, because that shows me the data flow and the conventions quickly. I check my assumptions by asking early instead of guessing for 20 minutes, and I keep a running list of weak spots while I read."

---

## Phase 2: Feature design (transfers)

### Step 0: Clarifying questions (ask these BEFORE designing)

| Ask | Likely answer | Why it matters |
|---|---|---|
| Only between the same customer's accounts, or any two accounts? | Any account for v1; auth is out of scope | Ownership check. Flag it as a v2 must-have. |
| What if the currencies differ? | Reject in v1 | No FX. Return a 422. |
| Can frozen or closed accounts send or receive? | Neither | Two separate status checks. |
| Synchronous and instant, or pending and settled later? | Synchronous | Keeps the state machine simple (`completed`). |
| Any limits? | Reuse the existing max amount | Reuse `parse_amount`. |
| Should transfers show up in `/transactions` history? | Yes | Decides whether to reuse `Transaction`. |
| How does the client send retries? | Same idempotency key | Header vs body, and how long keys are kept. |
| Single process, in-memory like the rest? | Yes | Decides between a lock and database transactions. |
| Can a transfer be cancelled or reversed? | Out of scope | Keeps v1 small. |

### Step 1: Break it into steps

1. **Models:** add a `Transfer` dataclass. Add an optional `transfer_id` to `Transaction` so both legs link back to the transfer. Keep a **separate internal set** `{"transfer_in", "transfer_out"}`, and **do not** add these to the public `TRANSACTION_TYPES`.
2. **Storage:** add a `transfers` dict, an `idempotency` dict mapping key → (request fingerprint, transfer_id), `next_transfer_id`, `transfers_for(account_id)` that matches **from OR to**, and a `threading.Lock`.
3. **Ledger:** `balance()` handles all four types: deposit and transfer_in add; withdrawal and transfer_out subtract. An unknown type should **raise**, not be ignored.
4. **Service:** `TransferService.create_transfer`. Do all validation first, then all writes, under the lock.
5. **API:** a `transfers` blueprint. Register it in `app.py`. Add a JSON catch-all 500 handler.
6. **Tests:** see Step 8.

### Step 2: API contract

**`POST /transfers`**, with header `Idempotency-Key: <uuid>` (a header is the common convention; a body field also works if you document it).
```json
{ "from_account_id": "acc_0001", "to_account_id": "acc_0002", "amount": "100.00", "memo": "rent" }
```
→ **201 Created**, with `Location: /transfers/trf_0001`
```json
{ "id": "trf_0001", "from_account_id": "acc_0001", "to_account_id": "acc_0002",
  "amount": "100.00", "currency": "USD", "status": "completed", "memo": "rent",
  "created_at": "2026-09-16T18:00:00+00:00" }
```
- **Why 201:** a new resource was created. **Why a string amount:** floats lose precision (0.1 + 0.2), and the existing API already sends amounts as strings.

**`GET /transfers/<id>`** → 200 with the transfer, or **404** JSON.

**`GET /accounts/<id>/transfers?page=1&page_size=20&direction=all|sent|received`** → 200
```json
{ "account_id": "acc_0001", "page": 1, "page_size": 20, "total": 3, "total_pages": 1,
  "transfers": [ { "...": "...", "direction": "sent" } ] }
```

### Step 3: Validation order and error codes (the part they drill hardest)

| # | Check | Code | Message |
|---|---|---|---|
| 1 | Body isn't a JSON **object** (missing, a list, a string) | 400 | request body must be a JSON object |
| 2 | `from_account_id` or `to_account_id` missing or not a string | 400 | from_account_id is required |
| 3 | Amount isn't a number, is NaN/Infinity, ≤ 0, has > 2 decimal places, or is > max | 400 | reuse `parse_amount` |
| 4 | Memo isn't a string or is too long | 400 | memo must be a string |
| 5 | `from == to` | 400 | cannot transfer to the same account |
| 6 | Idempotency key seen before with the **same** request | 201 (replay) | return the **original** transfer; move no money |
| 7 | Idempotency key seen before with a **different** request | 409 | idempotency key reused with different parameters |
| 8 | Source not found / destination not found | 404 | account X not found *(some argue 422 since the id is in the body; pick one and document it)* |
| 9 | Source not active | 409 | account X is frozen |
| 10 | Destination not active | 409 | account Y is closed |
| 11 | Currencies differ | 422 | currency mismatch: USD → EUR |
| 12 | `amount > balance(source)` (**equal is allowed**) | 422 | insufficient funds |
| 13 | Success | 201 | |
| — | Anything unexpected | 500 | JSON `{"error": "internal error"}` |

**Why the order matters:**
- **Cheap stateless checks first.** Don't look anything up for a garbage request.
- **Idempotency before state checks.** If the first attempt succeeded and the client retries, the balance is now lower, so the retry would wrongly get **422 insufficient funds**. The retry must replay the original success instead. This is a great point to bring up yourself.
- **Every check before any write.** That's how you stay atomic (next step).

### Step 4: Atomicity (the "fails halfway" question)

- **Never write the debit until everything is validated.** Build the `transfer_out` transaction, the `transfer_in` transaction, and the `Transfer` record as objects first, since that's where exceptions can happen (bad types, `.strip()`, and so on). Then do three appends that can't fail.
- In-memory: hold `store.lock` for the whole **re-check balance → write both legs → save transfer → save idempotency key** sequence.
- Real database: **one DB transaction**. Lock both account rows with `SELECT ... FOR UPDATE`, **always in sorted-id order** so two opposite transfers (A→B and B→A) can't deadlock. Commit both legs together. A double-entry rule (the legs of every transfer sum to zero) can be a DB constraint or a check.

### Step 5: Concurrency

Without a lock, two 3000 transfers out of a 4879.55 account both pass the balance check, and the account overdraws. The check and the write must happen under the same lock, or the same database transaction. Idempotency has its own race: two retries with the same key arrive together. Save the key as "in progress" under the lock, so the second one sees it (and returns 409 "request in progress", or waits).

### Step 6: Idempotency

- Key → `{fingerprint: hash(from, to, amount, memo), transfer_id, created_at}`.
- Same key, same fingerprint → return the stored transfer. Same key, different fingerprint → 409.
- Scope keys per client or account in a real system, and expire them after about 24h.
- Missing key: either **require** it (400) or accept requests without one and skip the protection. Say which you picked and why. Requiring it is the safer choice for a mobile client that retries.

### Step 7: Reusing `Transaction` and keeping the old endpoint safe

- **Reuse `Transaction`** for the two legs. Then `balance()` and `/transactions` history "just work," and there's one source of truth.
- **`POST /accounts/<id>/transactions` must NOT accept `transfer_in` or `transfer_out`.** Otherwise anyone can post a fake `transfer_in` and create money, or post a `transfer_out` that skips the overdraft check (that check only runs for `"withdrawal"`). Keep the transfer types internal.

### Step 8: Listing and pagination edge cases

- `offset = (page - 1) * page_size` (pages start at 1). **Sort order must be stable:** `created_at` descending, with `id` as a tiebreaker.
- `page` missing → 1. `page` < 1 or not an integer → **400**.
- `page_size` missing → 20. `page_size` < 1 or not an integer → **400** (never divide by zero). Over 100 → **400** or clamp to 100 (pick one and document it).
- A page past the end → **200 with an empty list**, not 404.
- Zero transfers → `total: 0`, `total_pages: 0`, and an empty list.
- Unknown account → 404. Unknown `direction` → 400.
- Must include **received** transfers, not just sent ones.
- Later: offset pagination shifts when new transfers arrive, so use cursor pagination (`?after=<id>`) at scale.

### Step 9: Testing

- **Invariant: the total of all balances is unchanged after any transfer.** Money is neither created nor destroyed. Also: no balance ever goes negative.
- Happy path: check the **source and destination** balances, **both** histories, `GET /transfers/<id>`, and that the transfer appears in **both** accounts' lists.
- One test per row of the error table, checking both the status code **and** that no state changed (balances and counts are the same as before).
- Boundary: transfer the exact balance → 201; balance + 0.01 → 422.
- Idempotency: same key twice → same id, money moves once. Same key with a different amount → 409.
- Pagination: page 1 has the newest items; the last page is partial; a page past the end is empty; page_size 0 → 400.
- Concurrency: N threads transfer out of one account; the final balance is ≥ 0 and the total is unchanged.

### Step 10: At scale, and out of scope

- **At scale:** a real database with transactions; a stored running balance; an idempotency table with a unique constraint; shared state across workers; publish a `transfer.completed` event (outbox pattern) for notifications; metrics and tracing on transfer success and failure reasons.
- **Out of v1:** currency exchange, scheduled or recurring transfers, reversals, external transfers (ACH or wire), daily limits, notifications, and **authorization**. Call out that authorization is a must before real launch.

---

## Phase 3: PR review

### How to review, out loud

1. **Overview:** read the ticket and list the changed files: `app.py`, `models`, `storage`, `services/ledger.py`, new `services/transfers.py`, new `api/transfers.py`, new tests.
2. **Completeness:** go through the ticket's requirements one by one and ask "where is this implemented?"
3. **Correctness:** trace `POST /transfers` end to end with **real seed data** and concrete numbers.
4. **Weak spots:** boundaries (`>` vs `>=`), the order of writes, the error paths, and pagination math.
5. **Tests:** what do they actually assert? "Tests passing" proves only what they check.
6. **Verdict and how you'd say it.**

**How to verify a bug without a terminal:** pick a concrete input from the seed data, walk it through the code line by line, and state *expected* vs *actual*. Then say: "I'd confirm by writing a quick test with this input."

### Requirement checklist

| Ticket requirement | Met? |
|---|---|
| 1. POST moves money | ❌ The source account is never debited (bug #1) |
| 2. Both balances **and** histories reflect it | ❌ Source balance unchanged; after a failed transfer the history has an orphan debit (bug #3) |
| 3. GET one transfer | ⚠️ Works, but a missing id gives 500 instead of 404 (bug #8) |
| 4. List sent **and** received, page starts at 1, max 100 | ❌ Received transfers missing, page 1 is empty, no max (bugs #10–12) |
| 5. Retries never move money twice | ❌ Key is stored but never checked (bug #4) |
| 6. Same currency; frozen/closed can't send **or** receive | ❌ Destination is never checked (bug #5) |
| 7. Existing error conventions | ❌ 200 instead of 201; several 500s |

### The bugs (all verified)

> Your friend's version had about **2 simple + 1 medium** bugs. This PR has more so you get extra reps. **If you only find three, find #1, #7, and #9 first, then #3.**

#### 🔴 Blocking: money is wrong

**#1 (Medium) Transfers create money: `balance()` never subtracts `transfer_out`.**
*Where:* `services/ledger.py`, `if transaction.type in ("deposit", "transfer_in")`. The `elif` still only handles `"withdrawal"`.
*Repro:* acc_0001 = 4879.55, acc_0002 = 5687.50. Transfer 100 from acc_0001 to acc_0002. Afterwards acc_0001 is **still 4879.55** and acc_0002 is **5787.50**. $100 appeared out of nowhere.
*Why the tests missed it:* `test_transfer_moves_money` only checks the **destination** balance.
*Fix:* `elif transaction.type in ("withdrawal", "transfer_out")`. Test that both balances change and that the total across all accounts is unchanged.

**#2 (Medium) Anyone can mint money through the old endpoint.**
*Where:* `models/models.py` adds `transfer_in` and `transfer_out` to the **public** `TRANSACTION_TYPES`, which `record_transaction` uses to validate client input.
*Repro:* `POST /accounts/acc_0003/transactions {"type": "transfer_in", "amount": "50000"}` → **201**, and the balance becomes 50750.00. After fixing #1, `"type": "transfer_out"` would also **skip the insufficient-funds check**, which only runs for `"withdrawal"`.
*Fix:* keep a separate internal set for transfer types. Public validation stays `{"deposit", "withdrawal"}`.

**#3 (Medium) Not atomic: the debit is written before the destination is validated.**
*Where:* `services/transfers.py`. `self.store.add_transaction(debit)` runs, then `self.ledger.get_account(to_id)`.
*Repro:* transfer from acc_0001 to `acc_9999` → **404**, but acc_0001's history now contains a `transfer_out` for money that went nowhere. Today #1 hides the balance effect. **Once #1 is fixed, this deletes customer money.** The same happens when `to_account_id` is missing.
*Fix:* validate everything first (both accounts, statuses, currency, funds), then write both legs and the transfer together under a lock.

**#4 (Simple to spot, severe) Idempotency isn't implemented.**
*Where:* `idempotency_key` is saved on the `Transfer` but never looked up.
*Repro:* the same request with `idempotency_key: "k1"` sent twice → `trf_0001` **and** `trf_0002`, and money moves twice. Requirement 5 isn't met. The test sends a key but never retries.
*Fix:* check the key before any state checks. On a match, return the original transfer; on a match with different parameters, return 409.

**#5 (Simple) Destination status and currency are never checked.**
*Repro:* acc_0001 (USD, active) → acc_0004 (**EUR, frozen**) → **200**. A frozen account received money, and USD was credited 1:1 as EUR.
*Fix:* 409 if the destination isn't active; 422 if the currencies differ.

**#6 (Simple) Same-account transfer is allowed.**
*Repro:* acc_0001 → acc_0001 → **200**. It creates junk records, and the balance check compares against itself.
*Fix:* 400 "cannot transfer to the same account", before any lookups.

#### 🟠 Blocking: wrong behavior at the edges

**#7 (Simple) Off-by-one: you can't transfer your exact balance.**
*Where:* `if amount >= self.ledger.balance(from_id)`.
*Repro:* acc_0003 balance = 750.00 (800 − 50). Transfer 750.00 → **422 insufficient funds**. Meanwhile `record_transaction` in the same codebase correctly uses `>`.
*Fix:* `amount > balance`, plus a boundary test.

**#8 (Simple) `GET /transfers/<id>` returns 500 for an unknown id.**
*Where:* `transfers().store.transfers[transfer_id]` raises `KeyError`, which falls through to an HTML 500. It also reaches into the store and skips the service layer.
*Fix:* add a service method `get_transfer` that raises `NotFoundError` → 404 JSON.

**#9 (Simple) Create returns 200, not 201.**
*Where:* `return jsonify(transfer.to_dict()), 200`. The rest of the codebase returns 201 for creates, and **the test asserts 200**, so the test locks the bug in.
*Fix:* return 201 (optionally with a `Location` header) and update the test.

#### 🟡 Blocking: pagination / chunking

**#10 (Medium) Page 1 skips the first chunk.**
*Where:* `start = page * page_size`. With pages starting at 1, page 1 begins at item 20.
*Repro:* acc_0001 with 1 transfer → `total_pages: 1`, `transfers: []`. **Page 1 is empty** for any account with 20 or fewer transfers, and the first 20 items can never be reached.
*Fix:* `start = (page - 1) * page_size`.

**#11 (Simple) Unvalidated page params crash or misbehave.**
*Repro:* `page_size=0` → `ZeroDivisionError` → **500**. `page=abc` → `ValueError` → **500**. `page=-1` → a negative slice returns wrong or empty results. There's no max, so `page_size=1000000` is accepted even though the ticket says max 100.
*Fix:* parse and validate: 400 for non-integers or values < 1, and enforce the max of 100.

**#12 (Medium) Received transfers aren't listed.**
*Where:* `storage/csv_store.py` `transfers_for` only matches `t.from_account_id == account_id`.
*Repro:* after acc_0001 → acc_0002, `GET /accounts/acc_0002/transfers` shows **0** transfers. Requirement 4 says sent **and** received.
*Fix:* match `from_account_id == id or to_account_id == id`, and consider adding a `direction` field.
*Why the test missed #10 and #12:* `test_list_transfers` only checks that the `"transfers"` key **exists**. An empty list passes.

#### ⚪ Non-blocking: quality and consistency

- **#13** `Transfer.to_dict` returns the amount as a **float** (`5.0`), but transactions return strings (`"5.00"`). That loses precision and makes the API inconsistent.
- **#14** No lock around check-then-write, so concurrent transfers can overdraw (same issue as the base code, but this feature makes it worse).
- **#15** A missing `from_account_id` gives 404 "account None not found" instead of 400 "from_account_id is required". The description is built with `to_id` before it's validated ("Transfer to None").
- **#16** `request.get_json(silent=True) or {}` means a JSON list body → 500. `memo` and `idempotency_key` aren't type-checked.
- **#17** `datetime.now(...)` is duplicated instead of using the ledger's `_now()`. The two legs have no `transfer_id` linking them to the `Transfer`. `list_for_account` sorts by `created_at` with no tiebreaker, and both legs share the same timestamp.
- **#18** The tests use the real `data/` CSVs instead of `create_app(data_dir=fixture)`, and only cover happy paths.

### Severity ranking (what blocks merge)

1. #1 and #3: money is created or destroyed.
2. #2: anyone can mint money through the public API.
3. #4: retries double-charge (a stated requirement).
4. #5 and #6: business rules are violated.
5. #10 and #12: listing is broken, so the requirement isn't met.
6. #7, #8, #9, #11: edge-case correctness and API conventions.
7. The ⚪ items: follow-ups or nits.

**Verdict: Request changes.** The core invariant (money is conserved) is broken, and 4 of 7 requirements aren't met.

### Tests to ask for

The conservation invariant (the sum of all balances is unchanged); **both** balances and **both** histories; exact-balance boundary; nonexistent destination leaves **no** writes; same-account → 400; frozen/closed destination → 409; currency mismatch → 422; idempotent retry → same id, money moves once; same key with a different amount → 409; `GET /transfers/unknown` → 404; posting `transfer_in` to `/transactions` → 400; pagination (page 1 has the newest items, a page past the end is empty, `page_size=0` → 400, over 100 → 400); received transfers appear in the recipient's list; and use a fixture `data_dir`.

### Example review comment (say it like this)

> **🔴 Blocking: transfers create money.** `balance()` now adds `transfer_in` but still only subtracts `withdrawal`, so the source account never goes down.
> **Repro:** acc_0001 starts at 4879.55. `POST /transfers` for 100.00 to acc_0002. acc_0001 is still 4879.55 and acc_0002 is 5787.50.
> **Impact:** every transfer adds money to the system.
> **Suggestion:** handle `transfer_out` next to `withdrawal`, and add a test asserting both balances plus the total across accounts. The current test only checks the destination, which is why it passed. Happy to pair on this if that helps!

A good comment has a **severity label, what's wrong, a concrete repro, the impact, a suggested fix, and a friendly tone.** Tell the author about the good parts too: "Nice reuse of `parse_amount`, and splitting the logic into a service keeps the blueprint thin."

### Delivering the verdict

> "Overall the structure is right: a service layer, reusing `Transaction` for the legs, and a separate blueprint. But I'd request changes before merging. There are two money-correctness bugs, and it doesn't meet the idempotency, listing, and destination-validation requirements. I left repro steps on each comment and a list of tests that would catch them. I'd rather we pair for 30 minutes than trade comments back and forth."

---

## Communication playbook

**Rules from someone who's taken it**
- Think out loud the **entire** time. Silence reads as stuck.
- Don't go line by line. Follow imports, trace one method (a POST is good), and summarize.
- Stop exploring at 20 minutes. Keep your summary ready by minute 18.
- In design: break it into steps, then go **deep** on each one, especially edge cases and **error codes**. Don't write code.
- In PR review: overview → requirements → trace end to end → find the weak spots → go through each weak spot.
- Expect them to drill into everything. "Why?" is a normal follow-up, not a sign you're wrong.
- Practice out loud with a friend or Claude. Have it play the interviewer and push on your answers.

**Datadog values to show (from the prep guide)**
- **Ownership:** "If I owned this, the next things I'd fix are…"
- **Pragmatism:** "Simplest fix is a lock. A database transaction is the real answer at scale."
- **Honesty and humility:** "I'm not sure if Flask's dev server is threaded by default. My guess is yes, can you confirm?"
- **Tie things to observability** when it fits naturally. It's their product.

**Phrases that score**
- "Let me confirm my assumption before I keep going…"
- "I'll pick one request and trace it end to end."
- "Here's a concrete input that breaks it, expected vs actual."
- "Is this correct? And is it *complete* against the ticket?"
- "I'm stuck on X. I've tried A and B, and my guess is C."

### Questions to ask them at the end

- "How does your team dogfood Datadog while building? Has that ever caught something before customers did?"
- "How do new interns get oriented in a codebase? Does it look like what we did today?"
- "What does ownership look like for an intern? Shipping to production, being on call, owning a feature?"
- "What separates interns who have a big impact from those who don't?"
- "What's a recent technical challenge on your team that came from scale?"
- "How does your team decide between proven, well-hardened tech and something newer?"
