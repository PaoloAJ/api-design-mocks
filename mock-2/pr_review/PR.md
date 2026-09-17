# PR: Add `POST /v1/incidents/{id}/escalate`

**Author:** a teammate · **Branch:** `feat/escalate-severity` → `main`

## Description

Right now there's no way to change an incident's severity after it's
created — if a sev3 turns out to be way worse than we thought, the only
option today is to close it out and open a brand-new incident, which loses
the whole timeline and confuses whoever's watching the original one.

This adds a single endpoint, `POST /v1/incidents/{id}/escalate`, that bumps
the severity up one level (e.g. `sev3` → `sev2`) and drops a note in the
timeline saying it happened. No request body needed — it just moves the
needle one notch each time you call it.

Tested manually against the running server and it does what you'd expect.
Didn't add automated tests since it's a pretty small change; happy to add
some if people want them before merge.

## Diff

```diff
--- a/app/api/incidents.py
+++ b/app/api/incidents.py
@@ -23,7 +23,8 @@ from flask import Blueprint, g, jsonify, request
 from app.core.errors import ConflictError, NotFoundError, ValidationError
 from app.core.pagination import paginate, parse_limit
 from app.core.store import store, utcnow
 from app.core.validation import (
+    SEVERITY_ORDER,
     SEVERITIES,
     STATUSES,
     validate_incident_create,
@@ -313,6 +314,25 @@ def transition_status(incident_id):
     response.headers["ETag"] = etag_for(updated)
     return response
 
 
+@bp.post("/<incident_id>/escalate")
+def escalate_incident(incident_id):
+    """Bump severity one level, e.g. sev3 -> sev2."""
+    incident = store.get("incidents", incident_id)
+
+    current_index = SEVERITY_ORDER.index(incident["severity"])
+    new_severity = SEVERITY_ORDER[current_index - 1]
+
+    incident["severity"] = new_severity
+    incident["updated_at"] = utcnow()
+
+    _add_timeline_entry(
+        incident_id, "escalated", "system",
+        "Escalated to {}".format(new_severity),
+    )
+
+    return jsonify(serialize(incident))
+
+
 @bp.post("/<incident_id>/responders")
 def add_responder(incident_id):
     """Naturally idempotent: adding an already-present responder is a no-op.
```

---

*Review this the way you would in the real interview: read it cold, say what
you'd say out loud, and only then open `REVIEW_KEY.md`. You get more out of
finding the issues yourself under time pressure than reading the key first —
give yourself 10 minutes before you peek.*
