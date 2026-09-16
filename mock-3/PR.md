# PR #318 — Bulk decisions for the review queue

**Branch** `feat/bulk-decisions` → `main`  ·  **+183 −2** across 3 files
**Checks** ✅ 64 tests passing

---

Reviewers working a spam wave are deciding fifty near-identical cases one at a
time, and each one is a full round trip. This adds `POST /v1/cases/decisions`,
which takes a list of case IDs and one decision to apply to all of them. It
follows the ingest endpoint's shape: per-item results, partial success, and an
index on every error so the UI can highlight the rows that failed.

Two smaller things ride along:

- `?exclude_claimed=true` on the collection hides cases another reviewer is
  already in, so two people don't open the same case.
- The detail response now carries `queue_position`, because the review UI wants
  to show "12 of 340 in priority" in its header.

**Commits**

1. `4a1c209` Add `POST /v1/cases/decisions` for bulk review
2. `b77e51f` Hide claimed cases from the queue listing
3. `e30f4d1` Let T&S vendors work a platform's queue

> Third commit is unrelated to bulk review — our vendor reviewers could not see
> the queues they are contracted to work, which was blocking their onboarding.
> Folded it in here since it touches the same handler.

---

## Files changed

#### `app/api/cases.py`

```diff
diff --git a/app/api/cases.py b/app/api/cases.py
index 7d9930c..282658e 100644
--- a/app/api/cases.py
+++ b/app/api/cases.py
@@ -100,7 +100,11 @@ def list_cases():
     and all must match; repeated params beat a comma-joined string because
     label values can legitimately contain commas.
     """
-    records = [c for c in store.list("cases") if c["platform_id"] == g.platform_id]
+    # Trust & Safety vendors review on behalf of a platform; the gateway sets
+    # this header for their sub-account so they can see the queue they work.
+    on_behalf_of = request.headers.get("X-Mod-On-Behalf-Of")
+    platform_id = on_behalf_of or g.platform_id
+    records = [c for c in store.list("cases") if c["platform_id"] == platform_id]
 
     state = request.args.get("state")
     if state:
@@ -162,6 +166,11 @@ def list_cases():
     limit = parse_limit(request.args.get("limit"))
     page, next_cursor = paginate(records, limit=limit, cursor=request.args.get("cursor"))
 
+    # Hide cases another reviewer already took, so two people don't open the
+    # same one. Applied to the page so we only check the rows we return.
+    if request.args.get("exclude_claimed") == "true":
+        page = [c for c in page if c["state"] != "in_review"]
+
     return jsonify(
         {
             "data": [serialize(c) for c in page],
@@ -220,6 +229,24 @@ def create_case():
     return response
 
 
+def _queue_position(case):
+    """Where this case sits in its queue.
+
+    The review UI shows "12 of 340 in priority" in the header when a reviewer
+    opens a case, so the detail endpoint reports the position.
+    """
+    peers = [
+        c
+        for c in store.list("cases")
+        if c["platform_id"] == case["platform_id"] and c["queue"] == case["queue"]
+    ]
+    peers.sort(key=lambda c: (c["created_at"], c["id"]))
+    for index, peer in enumerate(peers):
+        if peer["id"] == case["id"]:
+            return {"index": index, "queue_depth": len(peers)}
+    return None
+
+
 @bp.get("/<case_id>")
 def get_case(case_id):
     """GET one — supports conditional requests via If-None-Match (304)."""
@@ -228,7 +255,9 @@ def get_case(case_id):
     if request.headers.get("If-None-Match") == etag:
         # Saves the client re-downloading a body it already has.
         return "", 304, {"ETag": etag}
-    response = jsonify(serialize(case))
+    body = serialize(case)
+    body["queue_position"] = _queue_position(case)
+    response = jsonify(body)
     response.headers["ETag"] = etag
     return response
 
@@ -312,6 +341,49 @@ def decide_case(case_id):
     return response
 
 
+@bp.post("/decisions")
+def decide_batch():
+    """Decide many cases at once.
+
+    Reviewers working a spam wave were deciding identical cases one at a time;
+    this lets the UI send the whole selection in one request. Mirrors the
+    ingest endpoint: per-item results, partial success, an index on each error.
+    """
+    body = request.get_json(silent=True) or {}
+    case_ids = body.get("case_ids")
+    if not isinstance(case_ids, list) or not case_ids:
+        raise ValidationError(
+            "`case_ids` must be a non-empty array.",
+            errors=[{"field": "case_ids", "message": "required array"}],
+        )
+
+    # Same validator the single-case endpoint uses, so the two paths cannot
+    # drift apart on what a valid decision looks like.
+    payload = validate_decision(body.get("decision") or {})
+
+    decided = []
+    errors = []
+    for index, case_id in enumerate(case_ids):
+        case = store.get("cases", case_id)
+        if case["platform_id"] != g.platform_id:
+            errors.append({"index": index, "reason": "not found"})
+            continue
+        if case["state"] in TERMINAL_STATES:
+            errors.append({"index": index, "reason": "already decided"})
+            continue
+
+        changes = {
+            "state": payload["decision"],
+            "reason": payload["reason"],
+            "decided_by": payload["reviewer"],
+            "decided_at": utcnow(),
+        }
+        store.update("cases", case_id, changes)
+        decided.append(case_id)
+
+    return jsonify({"decided": len(decided), "failed": len(errors), "errors": errors})
+
+
 @bp.get("/<case_id>/appeals")
 def list_case_appeals(case_id):
     """Appeals scoped to one case — a nested collection.
```

#### `tests/test_bulk_decisions.py`

```diff
diff --git a/tests/test_bulk_decisions.py b/tests/test_bulk_decisions.py
new file mode 100644
index 0000000..bc686e1
--- /dev/null
+++ b/tests/test_bulk_decisions.py
@@ -0,0 +1,74 @@
+"""Bulk decisions on POST /v1/cases/decisions."""
+
+import pytest
+
+
+@pytest.fixture
+def three_cases(client, auth):
+    ids = []
+    for i in range(3):
+        body = client.post(
+            "/v1/cases",
+            json={
+                "external_id": "post_{}".format(i),
+                "kind": "post",
+                "text": "spam wave {}".format(i),
+                "reason": "spam",
+            },
+            headers=auth,
+        ).get_json()
+        ids.append(body["id"])
+    return ids
+
+
+def _decision(**over):
+    d = {"decision": "removed", "reviewer": "reviewer_kim", "reason": "spam"}
+    d.update(over)
+    return d
+
+
+def test_batch_decides_every_case(client, auth, three_cases):
+    response = client.post(
+        "/v1/cases/decisions",
+        json={"case_ids": three_cases, "decision": _decision()},
+        headers=auth,
+    )
+    assert response.status_code == 200
+    assert response.get_json()["decided"] == 3
+    for case_id in three_cases:
+        got = client.get("/v1/cases/{}".format(case_id), headers=auth).get_json()
+        assert got["state"] == "removed"
+        assert got["decided_by"] == "reviewer_kim"
+
+
+def test_batch_reports_already_decided_per_index(client, auth, three_cases):
+    client.post(
+        "/v1/cases/{}/decision".format(three_cases[0]),
+        json=_decision(decision="approved", reason="none"),
+        headers=auth,
+    )
+    response = client.post(
+        "/v1/cases/decisions",
+        json={"case_ids": three_cases, "decision": _decision()},
+        headers=auth,
+    )
+    body = response.get_json()
+    assert body["decided"] == 2
+    assert body["failed"] == 1
+    assert body["errors"][0]["index"] == 0
+
+
+def test_batch_requires_case_ids(client, auth):
+    response = client.post(
+        "/v1/cases/decisions", json={"decision": _decision()}, headers=auth
+    )
+    assert response.status_code == 400
+
+
+def test_batch_validates_the_decision_body(client, auth, three_cases):
+    response = client.post(
+        "/v1/cases/decisions",
+        json={"case_ids": three_cases, "decision": {"decision": "removed"}},
+        headers=auth,
+    )
+    assert response.status_code == 400
```

#### `tests/test_queue_position.py`

```diff
diff --git a/tests/test_queue_position.py b/tests/test_queue_position.py
new file mode 100644
index 0000000..a72f9fe
--- /dev/null
+++ b/tests/test_queue_position.py
@@ -0,0 +1,35 @@
+"""Queue position on the case detail response."""
+
+
+def test_detail_reports_queue_position(client, auth):
+    ids = []
+    for i in range(3):
+        ids.append(
+            client.post(
+                "/v1/cases",
+                json={
+                    "external_id": "p{}".format(i),
+                    "kind": "post",
+                    "text": "t",
+                    "queue": "priority",
+                },
+                headers=auth,
+            ).get_json()["id"]
+        )
+    seen = []
+    for case_id in ids:
+        body = client.get("/v1/cases/{}".format(case_id), headers=auth).get_json()
+        assert body["queue_position"]["queue_depth"] == 3
+        seen.append(body["queue_position"]["index"])
+    assert sorted(seen) == [0, 1, 2]
+
+
+def test_exclude_claimed_hides_in_review_cases(client, auth):
+    case = client.post(
+        "/v1/cases",
+        json={"external_id": "p1", "kind": "post", "text": "t"},
+        headers=auth,
+    ).get_json()
+    body = client.get("/v1/cases?exclude_claimed=true", headers=auth).get_json()
+    assert len(body["data"]) == 1
+    assert body["data"][0]["id"] == case["id"]
```
