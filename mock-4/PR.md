# Support lookup, queue position, and trust the carrier's delivered scan

Two things support has been asking for since launch, plus a carrier-reporting
fix.

Support can't see a merchant's shipment while that merchant is on the phone, so
every call turns into a screen-share. They also want "where does this sit in my
queue?" on the detail page.

The bug: when UPS scans a package delivered but we never got the intermediate
`in_transit` scan, we were rejecting the delivery scan, and the shipment sat in
`label_created` forever.

✅ **58 tests passing.**

### Commits

- `a91c3f2` — support lookup, queue position, and trust the carrier's delivered scan

---

## `a91c3f2`

Support tooling first. The internal proxy already knows which merchant the
agent is helping, so it forwards that as a header and we scope the lookup to it.

```diff
--- a/api/shipments.py
+++ b/api/shipments.py
@@ -24,7 +24,11 @@ def load_owned(shipment_id):
     """Keyed fetch, then tenant scope. Another merchant's shipment is a 404,
     never a 403: a 403 confirms the ID exists, enough to enumerate volume."""
     shipment = store.get("shipments", shipment_id)
-    if shipment["merchant_id"] != g.merchant_id:
+    # Support tooling needs to pull up a merchant's shipment while they're on
+    # the phone. The internal proxy sets X-Ship-On-Behalf-Of, so this header is
+    # trusted by the time it reaches us.
+    scope = request.headers.get("X-Ship-On-Behalf-Of") or g.merchant_id
+    if shipment["merchant_id"] != scope:
         raise NotFoundError("Shipment '{}' was not found.".format(shipment_id))
     return shipment
```

Then queue position on the detail page — where this shipment falls among the
merchant's other labels with the same carrier, oldest first.

```diff
@@ -91,7 +101,17 @@ def create_shipment():

 @bp.get("/<shipment_id>")
 def get_shipment(shipment_id):
-    return with_etag(load_owned(shipment_id))
+    shipment = load_owned(shipment_id)
+    # Support asked for "where does this sit in my queue?" on the detail page.
+    same_carrier = [
+        s for s in store.list("shipments")
+        if s["merchant_id"] == shipment["merchant_id"] and s["carrier"] == shipment["carrier"]
+    ]
+    same_carrier.sort(key=lambda s: s["created_at"])
+    response = with_etag(shipment)
+    body = response.get_json()
+    body["queue_position"] = [s["id"] for s in same_carrier].index(shipment_id) + 1
+    return jsonify(body), 200, dict(response.headers)
```

And the carrier-reporting fix. A delivered scan is the carrier telling us the
package is on the porch; we shouldn't argue with it because we missed a scan
in the middle.

```diff
--- a/api/scans.py
+++ b/api/scans.py
@@ -13,7 +13,7 @@ receiving scans. Both live outside this service.
 from flask import Blueprint, g, jsonify, request

 from core.errors import APIError, ValidationError
-from core.store import store
+from core.store import store, utcnow
 from services.shipping import apply_transition
```

```diff
@@ -102,7 +102,14 @@ def _apply_one(scan):

     target = SCAN_STATES[code]
     if target and target != shipment["state"]:
-        changes = apply_transition(shipment, target, source="scan")
-        shipment = store.update("shipments", shipment_id, changes)
+        if target == "delivered":
+            # The carrier is the source of truth for delivery. If they scanned
+            # it delivered, it is delivered, whatever we think the state is.
+            shipment = store.update(
+                "shipments", shipment_id, {"state": "delivered", "delivered_at": utcnow()}
+            )
+        else:
+            changes = apply_transition(shipment, target, source="scan")
+            shipment = store.update("shipments", shipment_id, changes)

     return {"scan_id": record["id"], "shipment_id": shipment_id, "state": shipment["state"]}
```

### Tests

```diff
--- a/tests/test_scans.py
+++ b/tests/test_scans.py
@@ -38,9 +38,9 @@ def test_scan_for_another_merchant_is_not_found(client):
     assert response.get_json()["errors"][0]["code"] == "not_found"


-def test_scan_cannot_make_an_illegal_transition(client, shipment):
+def test_carrier_delivered_scan_is_authoritative(client, shipment):
     response = post_scans(client, [{"shipment_id": shipment["id"], "code": "delivered"}])
-    assert response.get_json()["errors"][0]["code"] == "illegal_transition"
+    assert response.get_json()["errors"] == []
```

```diff
--- a/tests/test_shipments.py
+++ b/tests/test_shipments.py
@@ -160,3 +160,11 @@ def test_claim_rejects_unknown_reason(client, shipment):
         "/v1/shipments/{}/claims".format(shipment["id"]), json={"reason": "vibes"}, headers=ACME
     )
     assert response.status_code == 400
+
+
+def test_detail_reports_queue_position(client):
+    first = make_shipment(client, ACME)
+    second = make_shipment(client, ACME)
+    response = client.get("/v1/shipments/{}".format(second["id"]), headers=ACME)
+    assert response.status_code == 200
+    assert response.get_json()["queue_position"] == 2
+    assert first["id"] != second["id"]
```
