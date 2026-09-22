# Datadog SWE Intern Mock: Questions

> **How to use this:** Don't open `ANSWERS.md` until you've done each phase out loud. Set a timer for every phase. Talk the whole time as if someone is listening; record yourself if you can. The real interview is 1h30m: intro/project first, then these three phases, then your questions at the end.

The codebase is everything outside `interview_prep/`. It has no README, on purpose.

---

## Phase 0: Warm-up (≈10 min)

1. Tell me about yourself and what you're interested in working on.
2. Walk me through a project you're proud of. What was *your* part, and what was the hardest technical decision?
3. Tell me about a time you worked with others on that project. What went wrong, and what did you do about it?
4. What would you do differently if you rebuilt it today?

---

## Phase 1: Explore the codebase (⏱ 20 min hard cutoff)

### Prompt the interviewer gives you

> "This is a small internal service one of our teams owns. It's a ledger API for a retail bank. You've just joined the team. Take about 20 minutes to get oriented, and talk me through what you're doing and thinking as you go. Ask me anything. You don't need to run it."

**Rules for yourself:** don't read top to bottom or line by line. Start from the entry point, look at the imports, pick **one** request (a POST is a good choice), and trace it all the way down and back up.

### Questions the interviewer asks after you explore

**Orientation / overview**
1. In one or two sentences, what does this service do?
2. How did you decide where to start? Walk me through how you oriented yourself.
3. What's the job of each folder? Why do you think it's split up this way?

**Data flow**
4. Pick one endpoint and trace a request from the HTTP call to storage and back. Which functions does it touch, in order?
5. Where is data stored? What happens to a new deposit when the server restarts?
6. Where does an account's balance come from? Why might someone design it this way, and what's the trade-off?
7. How are IDs generated for new accounts and transactions? Can that break?

**Errors and API design**
8. How does an error raised deep in the service layer become an HTTP response?
9. The code uses 400, 404, 409, and 422. When does each one happen, and do you agree with the choices?
10. What happens if a client sends each of these to `POST /accounts/acc_0003/transactions`?
    - a) `{"type": "deposit", "amount": "NaN"}`
    - b) `{"type": "deposit", "amount": "10.005"}`
    - c) `{"type": "withdrawal", "amount": "750.00"}` (the account's balance is exactly 750.00)
    - d) a body that is a JSON **list** instead of an object
    - e) `{"type": "deposit", "amount": "5", "description": 42}`
    - f) the same request, but for `acc_0004`
11. `GET /accounts/acc_0001/transactions?type=bogus`: what comes back? Is that the right behavior?

**Weak spots / judgment**
12. Do you see any inconsistencies between the layers, or places where a layer is skipped?
13. If this went to production with real traffic, what would you worry about first? Rank your top three.
14. Two withdrawal requests for the same account arrive at the same time. What can happen?
15. How would you test this service end to end? What are the first 5 tests you'd write?
16. This is Datadog: how would you know this service is healthy in production? What would you log or measure?

**Communication / how you work**
17. What questions did you want to ask me while exploring, and why did they matter?
18. Say you'd been stuck for 15 minutes on something in here. How would you ask a teammate for help, and what would you say?
19. What kind of engineer are you when you land in an unfamiliar codebase?

---

## Phase 2: Design a feature (≈25 min, talk it through, don't write code)

### Prompt the interviewer gives you

> "Product wants customers to be able to **move money between accounts**. Customers should be able to make a transfer, look up a transfer, and see the transfers on an account. The mobile app retries requests when the network times out. Design this on top of the existing codebase. Talk me through it at a high level. No need to write code."

**Rules for yourself:** ask clarifying questions *first*. Break the design into steps, then go deep on each step. Cover every edge case and give each one an error code.

### Drill-down questions the interviewer asks

**Scoping**
1. What did you need to clarify before you started designing?
2. Break the work into steps. What would you build first?

**API contract**
3. What endpoints would you add? Give the method, path, request body, and response body for each.
4. What status code does a successful create return, and why?
5. How would you represent the amount in JSON, and why?

**Data model and storage**
6. What changes in `models/`, `storage/`, and `services/`? Which layer owns which logic?
7. How do balances and transaction histories reflect a transfer? Do you reuse the existing `Transaction` model?
8. How do you link the two sides of a transfer together?

**Correctness**
9. List every edge case you can think of, with its status code and error message.
10. In what order do you run the validations, and why does the order matter?
11. What happens if something fails halfway through, after you've debited the source but before you've credited the destination?
12. How do you stop two concurrent transfers from overdrawing the same account?
13. How do you make retries safe? Where does the idempotency key live? What if the same key comes back with a *different* amount?
14. Should the existing `POST /accounts/<id>/transactions` endpoint be able to create transfer-type transactions? Why or why not?

**Listing**
15. How do you design `GET /accounts/<id>/transfers`? What about pagination, and what are the edge cases for `page` and `page_size`?

**Testing, scale, and follow-ups**
16. How would you test it? What single invariant would you assert?
17. If this had to run across many servers with a real database, what changes?
18. What would you deliberately leave out of v1?

---

## Phase 3: Review a PR (≈20 min)

### Prompt the interviewer gives you

> "A teammate implemented transfers. Open `interview_prep/PR_REVIEW.md`. It has the ticket, the description, and the diff. Review it like a real PR. Tell me whether you'd approve it, and what you'd comment."

**Rules for yourself:** get an overview first, meaning the ticket and the list of files. Then check the ticket's requirements one by one (completeness). Trace one request end to end through the diff (correctness). Look for the weak spots and walk through each one. Don't trust "tests passing."

### Questions the interviewer asks

1. Summarize what this PR does in a sentence or two.
2. Does it meet every requirement in the ticket? Go through them one by one.
3. Walk me through `POST /transfers` end to end in the new code.
4. What bugs did you find? For each: where it is, a concrete input that triggers it, and the impact.
5. Rank the issues by severity. Which ones block merging?
6. How would you *verify* that each bug is real before commenting?
7. The tests pass. Why didn't they catch these bugs? What tests would you ask for?
8. Look at the pagination code specifically. Is it correct?
9. Write out (say out loud) the review comment you'd leave on the worst bug.
10. Approve, comment, or request changes? How do you deliver that to the author?

---

## End of interview: your questions for them (≈5 min)

Have 3 ready. See the end of `ANSWERS.md` for ideas.
