# Review key — `feat/escalate-severity`

Don't read this until you've reviewed `PR.md` cold. This mirrors the real
interview's phase 3: you're evaluated on *finding* problems and explaining
their impact, not on already knowing this list.

Bugs below are ordered by severity, matching how you should prioritize
comments in a real review — lead with the one that actually breaks
something, not the first one you happened to spot reading top to bottom.

---

## 1. Tenant isolation is missing (correctness + security)

```python
incident = store.get("incidents", incident_id)
```

Every other handler in this file fetches through `_get_owned()`, which
checks `incident["org_id"] != g.org_id` and 404s if another tenant owns the
resource. This one calls `store.get` directly. An `org_beta` caller who
learns or guesses an `org_alpha` incident ID can escalate it — and the
response body hands back the full incident, including `responders`,
`summary`, and `tags`, which is a cross-tenant data leak, not just an
unauthorized write.

**How you'd catch it live:** notice the pattern — every sibling function
above this one calls `_get_owned(incident_id)` first; this one doesn't call
it at all. That's a five-second scan, not a deep trace, which is exactly why
it's the first thing to flag.

**The fix:** `incident = _get_owned(incident_id)`.

## 2. Escalating past `sev1` silently de-escalates to `sev4` (correctness)

```python
current_index = SEVERITY_ORDER.index(incident["severity"])
new_severity = SEVERITY_ORDER[current_index - 1]
```

`SEVERITY_ORDER = ["sev1", "sev2", "sev3", "sev4"]`. If `incident["severity"]
== "sev1"`, `current_index == 0`, and `SEVERITY_ORDER[0 - 1]` is
`SEVERITY_ORDER[-1]` — Python's negative-index wraparound — which is
`"sev4"`. Calling "make this worse" on your most severe incident makes it
your *least* severe one. This is the kind of bug that passes a happy-path
manual test ("I escalated a sev3 to a sev2, looks right!") and only shows up
on the boundary nobody tried.

**The fix:** guard the boundary explicitly and decide the right behavior —
probably a `409` (`already_at_max_severity` or similar), not a silent wrap,
and not a `400` either since the request was well-formed when it was made
(the *state* makes it invalid now, which is what `409` is for — see
`mock-1`'s `README.md` on this exact distinction, and this repo's own
`acknowledge_alert`-style precedent isn't present here but `transition_status`
raising `ConflictError` for illegal moves is the pattern to match).

## 3. Bypasses `store.update` entirely (correctness + concurrency)

```python
incident["severity"] = new_severity
incident["updated_at"] = utcnow()
```

`store.get()` returns a **copy** of the record (see `Store.get` — `return
dict(record)`), so mutating it does nothing to the actual stored data...
except that `store._data[kind]` holds the *same nested structure* only if
nothing copies it deeper — worth tracing through with the candidate rather
than asserting: `dict(record)` is a shallow copy, and `severity` is a string
(immutable, reassigned, not mutated-in-place), so `incident["severity"] =
new_severity` only changes the local copy and **is silently lost** — the
response looks right, but nothing persisted. Confirm this by calling the
endpoint twice: the second call starts from the same original severity, not
the one the first call claimed to set.

Beyond the persistence bug, this line also entirely sidesteps `store.update`,
which is where `version` gets incremented and the lock is (re-)acquired.
Even a version that *fixes* the persistence bug by reaching into
`store._data` directly would still be wrong: no version bump means the
returned `ETag` is unchanged, so a client polling with `If-None-Match` never
sees the new severity, and a concurrent `PATCH` using an `If-Match` from
before the escalation wouldn't be invalidated the way it should be.

**The fix:** `store.update("incidents", incident_id, {"severity": new_severity})`
and use its return value both for the response and for computing the next
`ETag`.

## 4. No guard against escalating a resolved incident (product correctness)

Nothing stops `POST /v1/incidents/{id}/escalate` on an incident whose status
is `resolved`. Every other state-changing endpoint in this file cares about
the incident's current status — `transition_status` has `ALLOWED_TRANSITIONS`
specifically to prevent nonsensical moves. Escalating a resolved incident's
severity is exactly the same category of nonsensical, and the PR doesn't
consider it at all.

**The fix:** reject with `409 invalid_state_transition` (reusing the code
`transition_status` already uses) if `incident["status"] == "resolved"`.

## 5. No response `ETag` header (consistency)

Every other mutating handler in this file (`patch_incident`,
`transition_status`, `add_responder`) sets `response.headers["ETag"] =
etag_for(updated)` before returning. This one returns a bare `jsonify(...)`
with an implicit `200` and no `ETag` at all — inconsistent with the rest of
the file, and combined with bug #3, means there's no way for a client to
detect this change happened through the normal conditional-request
mechanism this API establishes everywhere else.

## 6. Zero test coverage for a new state-changing endpoint

The PR description says "tested manually against the running server" as the
verification story for a mutating endpoint. That's the tell to pull on in
review: ask what "tested" covered. It's very unlikely manual testing tried
escalating a `sev1` (bug #2) or a `resolved` incident (bug #4), or checked
whether a second `GET` reflects the change (bug #3) — the kind of edge cases
a few `pytest` cases targeting this file's existing fixtures
(`tests/test_state_machine.py` has the pattern to copy) would have caught
immediately. This is the completeness half of "does it work *and* does it do
what was asked" — a PR with no tests for new state-changing behavior is
itself a finding, independent of whatever bugs happen to be in it.

---

## How to grade a candidate's pass

- **Weak** — reads the diff, restates what it does, finds nothing wrong
  unprompted.
- **Adequate** — finds #1 and #3 (the two that are visible just from knowing
  this file's established patterns — `_get_owned` and `store.update` are
  used *everywhere else*) without needing the boundary case pointed out.
- **Strong** — also finds #2 unprompted (requires actually tracing
  `SEVERITY_ORDER[current_index - 1]` against `sev1`, not just skimming) and
  raises #6 as a review comment, not just a bug list — i.e., says "I'd ask
  for tests before approving this," which is the actual deliverable of a
  code review, not just spotting issues.
- **Exceptional** — proposes the fix for #4 unprompted by pattern-matching
  against `ALLOWED_TRANSITIONS` elsewhere in the same file, showing they're
  reading this PR *in the context of the codebase's own conventions*, which
  is exactly what "review a PR built on the same codebase" is testing for —
  not "can you find bugs in isolation," but "can you tell when new code
  doesn't match the standards the rest of the file already set."

## If you want to reproduce it locally

The diff isn't applied anywhere in this repo — `app/api/incidents.py` is
still the clean version. If you want to see the bugs actually fail:

1. Apply the diff by hand to a scratch copy of `app/api/incidents.py`.
2. Add `from app.core.validation import SEVERITY_ORDER` if you skip the
   import-block diff.
3. Run the server and hit `POST /v1/incidents/{sev1_id}/escalate` twice in a
   row — the second response is identical to what a `GET` would show before
   either call, confirming bug #3.
4. Escalate a `sev1` incident once and watch it come back `sev4`.

Don't leave this applied to the real `incidents.py` afterward — it's meant
to stay a read-only artifact so the next time you (or someone else)
practices this repo, the PR is unspoiled.
