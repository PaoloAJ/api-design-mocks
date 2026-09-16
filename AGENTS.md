# Building a mock case

Instructions for an agent asked to create a new mock interview case in this
repo. Read this fully before writing any code.

A **case** is a small, self-contained web app plus an interviewer guide. It is
the shared reference for a ~60 minute verbal mock interview. `mock-1/` is the
reference implementation — read it before building a second one.

---

## 1. What the round actually is

This is modeled on a real Datadog SWE intern loop. The format, verbatim from
the recruiting slide:

> **This is not a LeetCode interview.** ~60 minutes with an engineer, working
> inside a real web app codebase.
>
> **First 20 minutes:** background, a project you built, what you want out of
> the internship.
>
> 1. **Explore the codebase** — dropped into a web app you have never seen.
>    Get oriented, then explain how data moves.
> 2. **Design a feature** — talk through how you would add something new.
>    High level and out loud. No whiteboard.
> 3. **Review a PR** — read a real pull request on that codebase. Say what is
>    off and why it matters.
>
> **If you study one thing, study API design.** REST API design comes up in
> every phase.

That leaves roughly **40 minutes for all three tasks — about 13 minutes each.**
Every sizing decision below follows from that number.

### What candidates who sat it reported

These are first-hand and they constrain the design more than the slide does:

> "API round def study restAPI design. Really important. I would practice by
> building a flask app in python and creating endpoints for the app.
> Understand client/server interactions and all the HTTP methods,
> idempotency, etc."

> Asked whether deep or high-level understanding was needed, and whether it
> was about tracking how data comes in through a service:
> **"Yes, I would say that. And yes exactly how you say, understanding how
> data flows through the app was the biggest hurdle."**

> "It was very simple. Not many files at all. **The platform had no
> functionality for searching for words, so you had to look through
> everything.**"

> "The mistakes were things like **iterating over entire things when you don't
> need to**, and **doing things that the design spec specifically told you not
> to do**. So when you get there, make sure to review the design spec and
> check for any things they said not to do."

Four hard requirements fall out of those quotes. They are not suggestions:

| Report | Requirement |
|---|---|
| "no functionality for searching for words" | **The case must be navigable by reading, not grepping.** No candidate-facing README. Structure has to be legible from the file tree alone. |
| "not many files at all" | Hard ceiling on size. See §3. |
| "data flow was the biggest hurdle" | There must be **one traceable path** through the whole system that crosses an async boundary. This is the spine of the case. |
| "iterating over entire things", "design spec said not to do" | PR defects must include **an unnecessary full scan** and **a spec violation**. See §7. |

That last row is the most commonly missed. A case whose PR contains only
correctness bugs does not match the reported round.

---

## 2. What you are producing

```
mock-N/
  app/                  the application
  tests/                a passing suite
  requirements.txt      two dependencies, ideally
  wsgi.py               one command to run it
  INTERVIEWER.md        the guide — answer keys, questions, grading
  SPEC.md               the design spec (see §6; required)
  PR.md                 the pull request for task 3 (see §7; required)
  .gitignore
```

**There is no candidate-facing README.** Do not write one. The interviewer
walks the candidate through what the app does out loud; the candidate reads
code. `mock-1/README.md` exists only because that case doubles as a
self-study artifact — a case built for live interviewing should not have one.

Everything the interviewer needs lives in `INTERVIEWER.md`. Everything the
candidate is *told* comes out of the interviewer's mouth. The only files handed
to the candidate are the app itself, `SPEC.md`, and `PR.md`.

---

## 3. Size limits

Calibrated against `mock-1`: **1,495 lines of app code across 12 files**, two
dependencies, ~48 tests.

| | Target | Hard ceiling |
|---|---|---|
| App code (excl. tests) | 1,000–1,500 lines | 1,800 |
| Files under `app/` | 8–12 | 15 |
| Third-party dependencies | 2 | 3 |
| Directory depth below `app/` | 1 | 1 |
| Tests | 40–60 | — |

The candidate has ~3 minutes to orient before the first question. If the tree
does not fit on one screen, the case is too big.

**Depth matters more than line count.** `app/api/` and `app/core/` is the right
shape: one directory of route handlers, one of shared machinery. A third level
of nesting makes the app unreadable without search, which the candidate does
not have.

---

## 4. Choosing a domain

The domain must support a **data flow with an asynchronous boundary** — a point
where the system accepts something, returns before the work is finished, and
completes it elsewhere. That boundary is what makes "trace a request" a real
question instead of a reading exercise. It's where `202 Accepted` lives, where
data can be lost, and where a queue belongs.

`mock-1` uses observability: metrics are ingested (`202`, async), evaluated
against thresholds, and produce alerts that a human acknowledges.

Other domains that work:

| Domain | Async boundary | Human action at the end |
|---|---|---|
| Payments | authorize → capture → settle | refund |
| Shipping | label created → in transit → delivered | file a claim |
| Moderation | content submitted → queued for review → decided | appeal |
| CI | build queued → running → finished | retry a failed job |
| Email | send accepted → queued → delivered/bounced | resend |

Requirements for whichever you pick:

- **Three to four resources, no more.** `mock-1` has monitors, alerts, series.
- **One state machine** with transitions that have side effects. This is what
  justifies an action sub-resource (`POST /resource/{id}/action`) over a plain
  field write, which is a top-three interview topic.
- **One ingest endpoint** that behaves differently from the CRUD ones — bulk,
  partial success, `202`.
- **Two deliberate gaps.** `mock-1` leaves out evaluation and notification.
  Strong candidates notice and ask about them; weak ones narrate them as if
  they exist. This is one of the best signals in the case, so build it in.

Avoid domains a candidate might know professionally — the round tests reasoning
from code, not recall.

---

## 5. What the app must contain

Every item here exists to make a specific interview question askable. Skip one
and you lose the question.

**Required:**

- **Middleware chain** as `before_request` hooks — request ID, auth, rate
  limit — not per-route decorators. Ordering must be defensible (auth before
  rate limiting, so the counter keys on a known identity). This is the first
  thing a candidate should find when tracing a request.
- **Multi-tenancy** with a resource owned by another tenant returning `404`,
  not `403`. Enables the information-leak question.
- **Cursor pagination**, not offset. The cursor should encode a sort key and
  be trivially forgeable (base64, unsigned) so the security question is live.
- **Optimistic concurrency** — a version counter surfaced as an ETag, with
  `If-Match` giving `409`.
- **Idempotency** on at least one `POST`, via header, with **deliberate gaps**:
  no TTL, no body fingerprint, no in-flight handling, not tenant-scoped.
- **One error envelope** for every failure, including the framework's own 404s
  and unhandled exceptions. Validation collects all field errors, not the first.
- **An action sub-resource** whose side effects justify it not being a `PATCH`.
- **Bulk ingest** with partial success and an index on each error.
- **A liveness endpoint** that checks nothing, with no readiness counterpart.
- **In-memory storage behind a lock.** No database. The subject is the HTTP
  contract, and it keeps setup to `pip install` + run.

**One planted logic gap.** `mock-1` has `no_data`: a valid state that falls
through every branch in the transition handler, so the status changes but no
alert opens and existing ones never resolve. It is reachable, untested, and
invisible unless you read the enum against the branches. Build one equivalent.

### Code style

The code must **teach while being read** — a candidate has minutes, not hours.

- **Docstrings explain *why*, never *what*.** `mock-1`'s pagination module
  opens by explaining why offsets drift under concurrent writes. That is the
  model. A docstring restating the function name is wasted space in a case
  where reading time is the binding constraint.
- **Comment the tradeoffs, not the syntax.** Where the code picks one of two
  defensible options, say which and why in a line or two. These comments are
  what the candidate reacts to, and disagreeing with one well is a strong
  signal.
- **Name things so the tree is a map.** `store.py`, `pagination.py`,
  `validation.py`, `middleware.py`, `errors.py`. A candidate who cannot grep
  navigates by filename.
- **No cleverness.** No metaclasses, no decorators-generating-decorators. Every
  line should be readable by someone who has never seen the framework.

---

## 6. SPEC.md — required

A reported failure mode was *"doing things that the design spec specifically
told you not to do."* That requires a spec with explicit prohibitions, and a PR
that violates one.

`SPEC.md` is short (one page), candidate-visible during the PR task, and
contains a section of **explicit constraints** phrased as numbered rules:

```markdown
## Constraints

These are binding. A change that violates one is wrong even if its tests pass.

1. Handlers MUST NOT load a collection to answer a request about a single
   record. Use `store.get()` or `find_one()`.
2. Filtering MUST happen before pagination, never after.
3. Tenant scoping MUST derive from the authenticated identity in `g`, never
   from a request header or body field.
4. Cursors MUST be rejected with 400 if replayed against a different ordering
   than the one that issued them.
5. Sort keys MUST be immutable for the life of a paging walk.
```

Write five to eight such rules. **Number them** — it lets the interviewer ask
"which rule does this break?" and lets the answer key cite one. Make at least
two of them the kind a plausible PR would break. The spec is the interviewer's
leverage: "the spec says X, what did this PR do?" turns a vague code-review
answer into a concrete one.

`mock-1/SPEC.md` has eight rules; its PR violates six.

---

## 7. PR.md — the code review task

**Ship the PR as a static file — `PR.md` — not a git branch.**

The candidate reads `PR.md` the way they would read a PR page on GitHub. Keep
it in that shape: a title, a short description written in the voice of the
author, then the diff in fenced ```diff blocks, file by file, with a few lines
of surrounding context around each hunk.

Reasons this beats a branch:

- **The candidate has no search.** A branch invites `git log`, `git blame` and
  IDE diff tooling, which is exactly the navigation the round is supposed to
  deny them. A file is read top to bottom, like the rest of the case.
- Nothing to check out, no risk of the working tree being on the wrong branch
  when the session starts, no chance of them reading the merged result instead
  of the change.
- The interviewer can hand it over at minute 27 without touching the repo.

**The diff must apply cleanly against the real code.** Generate it from actual
edits on a scratch branch, extract the patch, then delete the branch and keep
only the file. A diff with invented line numbers or context that does not match
the files falls apart the moment a candidate opens the file it claims to
change.

**State the test result in the PR description** — "✅ 54 tests passing" — since
there is no CI to look at. Green tests are essential: a PR that visibly fails
is findable without reading it, which defeats the exercise.

### Required defect mix

Six or seven defects across three tiers, and the mix must include the two the
reports name explicitly:

| Tier | Count | Purpose |
|---|---|---|
| Surface | 1–2 | Everyone should find these. A candidate who misses them did not read the diff. |
| Mid | 2–3 | Requires domain reasoning. Separates adequate from good. |
| Deep | 1 | Silent wrong behavior with a `200` response. Separates good from excellent. |

**Two are mandatory regardless of tier:**

1. **An unnecessary full scan** — loading or iterating a whole collection when
   a keyed lookup would do. Reported verbatim as a real failure. `mock-1`'s PR
   adds a `position` field to the single-record `GET` that loads and sorts the
   tenant's entire collection to compute one integer.
2. **A spec violation** — something `SPEC.md` explicitly prohibits. Reported
   verbatim as a real failure.

Strongly recommended as the **severity test**: one defect that is a *security*
problem, not just a correctness one — a tenant-isolation bypass, a forgeable
token trusted, an auth check skipped. `mock-1/PR.md` reads `X-Dd-Support-Org`
off the request and uses it for tenant scoping, with a comment claiming the
internal proxy sets it. It is four lines and it hands any customer another
tenant's data.

This is the best discriminator in the whole case: **a candidate who lists six
defects flat has reviewed a diff; one who leads with the security defect and
says why it outranks the others has done code review.** The slide asks for
"what is off **and why it matters**" — that second clause is what this tests.

### Construction rules

- **Show it as two or three commits** in the PR description, and put a defect
  in a later one. Make it a plausible follow-up ("let support tooling list
  monitors"), not "fix review comments" — late small commits get waved through
  in real life, and the defect should ride in on that.
- **Every defect must reproduce.** Write a script that demonstrates each one
  and paste the output into the interviewer guide. A defect you cannot
  demonstrate is one you cannot grade.
- **Include two or three false positives** — things that look wrong and are
  not. A redundant-seeming identity mapping that is actually the right seam; a
  call that takes defaults that happen to be correct. Reviewing has a
  false-positive cost, and a candidate who flags everything has not shown
  judgment.
- **If a pre-existing test must change, that is itself a signal.** A changed
  test usually means a changed contract. Leave it visible in the diff.

---

## 8. INTERVIEWER.md

The only document the interviewer needs during the session. Required sections:

1. **Format** — the three tasks and the ~13-minute budget. State plainly that
   no code is written and that the candidate has no README and no search.
2. **Dataflow summary** — an ASCII diagram of the request lifecycle, the state
   machine, and the ingest path. This is the answer key for task 1 and the
   single most-consulted page. Put it early.
3. **The trace question, verbatim.** One question that walks the full path
   end to end, phrased concretely (a specific actor doing a specific thing),
   with weak/adequate/strong grading bands and four escalating follow-ups.
   **This is the question the round exists to ask.** Mark it as non-cuttable.
4. **Design questions** for task 2 — three or four, each with a concrete
   right-ish answer rather than open architecture chat. The best ones have a
   hidden coupling: adding sorting breaks cursor pagination, and finding that
   coupling *is* the answer.
5. **PR answer key** — every defect, its tier, the file and line, the `SPEC.md`
   rule it breaks, how to reproduce it, and what a strong candidate says about
   it. Plus the false positives, labeled.
6. **Signals** — strong and weak, as concrete behaviors rather than adjectives.
   "Asks how pagination works before designing sorting" beats "shows
   curiosity".
7. **Timing table** and an explicit cut order for when the session runs long.

Write it for someone reading it **while a candidate is talking.** Tables and
diagrams over prose. If a section cannot be scanned in ten seconds, compress it.

---

## 9. Verification

Before declaring a case done:

```bash
cd mock-N && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q                      # must be green
python wsgi.py                 # must start on first try
```

Then confirm, and record the evidence in `INTERVIEWER.md`:

- [ ] Every planted defect reproduces via a script you actually ran
- [ ] The planted logic gap is reachable and untested
- [ ] `PR.md` exists and its diff applies cleanly — verify with
      `git apply --check --directory=mock-N <patch>` **from the repo root**.
      A nested case directory is not its own git repo; running `git apply`
      from inside it silently matches against the parent's paths and reports
      success for a patch that was never applied.
- [ ] Tests are green with the PR patch applied, and the count in `PR.md`
      matches
- [ ] The repo has one branch; no scratch PR branch is left behind
- [ ] App code is under the §3 ceilings — measure, don't estimate
- [ ] No candidate-facing README exists
- [ ] `SPEC.md` prohibits something the PR does, and the answer key cites the
      rule number
- [ ] The trace question's full path is traceable in the code you actually
      wrote, not the one you designed
- [ ] You can read the whole app yourself in ten minutes without searching

That last check is the real one. If you need to grep to find where something
happens, so will the candidate — and they can't.

---

## 10. Port note

Bind to **5001**, not 5000. macOS AirPlay Receiver occupies 5000 and returns an
empty `403`, which looks exactly like an auth bug in the app and will burn
interview minutes.
