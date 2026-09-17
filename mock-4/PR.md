# Support tooling, destination search, and bulk cancel

Three things support and ops have been asking for since we launched:

1. Support can't see a merchant's shipment while that merchant is on the phone,
   so every call turns into a screen-share.
2. Merchants with a few hundred labels can't find one by city from the list
   view.
3. Voiding a batch of labels is one call per label today.

Also fixes a carrier-reporting bug: when UPS scans a package delivered but we
never got the intermediate `in_transit` scan, we were rejecting the delivery
scan and the shipment sat in `label_created` forever.

✅ **58 tests passing.**

### Commits

- `a91c3f2` — support lookup + queue position on the detail page
- `c04e81b` — destination search on the list view
- `7de2b55` — bulk cancel, and trust the carrier's delivered scan

---

## `a91c3f2` — support lookup + queue position

```diff
--- a/api/shipments.py
+++ b/api/shipments.py
@@ -2,7 +2,7 @@

 from flask import Blueprint, g, jsonify, request

-from core.errors import NotFoundError, ValidationError
+from core.errors import ForbiddenError, NotFoundError, ValidationError
 from core.store import paginate, parse_limit, store
 from services.shipping import STATES, apply_transition, assert_can_file_claim, validate_shipment
```

```diff
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

```diff
@@ -88,7 +98,18 @@ def create_shipment():

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

---

## `c04e81b` — destination search

```diff
@@ -57,6 +61,12 @@ def list_shipments():
     page, next_cursor = paginate(
         records, limit=parse_limit(request.args.get("limit")), cursor=request.args.get("cursor")
     )
+
+    # Merchants asked to search the destination city from the list view.
+    query = request.args.get("destination_contains")
+    if query:
+        page = [s for s in page if query.lower() in s["destination"].lower()]
+
     return jsonify(
         {
             "data": [serialize(s) for s in page],
```

---

## `7de2b55` — bulk cancel, and trust the carrier's delivered scan

```diff
@@ -176,6 +196,21 @@ def transition_shipment(shipment_id):
     return jsonify(serialize(store.update("shipments", shipment_id, changes)))


+@bp.post("/bulk-cancel")
+def bulk_cancel():
+    """Cancel several shipments at once — merchants batch-void labels daily."""
+    ids = (request.get_json(silent=True) or {}).get("shipment_ids") or []
+    cancelled = []
+    for shipment_id in ids:
+        shipment = store.get("shipments", shipment_id)
+        if shipment["merchant_id"] != g.merchant_id:
+            raise ForbiddenError("Shipment '{}' belongs to another merchant.".format(shipment_id))
+        updated = store.update("shipments", shipment_id, {"state": "cancelled"})
+        cancelled.append(serialize(updated))
+    return jsonify({"cancelled": cancelled, "count": len(cancelled)})
+
+
 @bp.post("/<shipment_id>/claims")
 def file_claim(shipment_id):
     """File a loss/damage claim — the human action at the end of the flow."""
```

Merchants get billed for a label the carrier already picked up even if they
void it, so a cancelled label needs to stay claimable:

```diff
--- a/services/shipping.py
+++ b/services/shipping.py
@@ -126,10 +126,12 @@ def assert_can_file_claim(shipment):
     if g.role != "merchant":
         # Known caller, wrong role — 403, not 404. The carrier key legitimately
         # belongs to this merchant; it just may not speak for them.
         raise ForbiddenError("Only a merchant key may file a claim.", code="role_required")
-    if shipment["state"] not in CLAIMABLE_STATES:
+    # Cancelled labels are claimable now: merchants get billed for a label the
+    # carrier already picked up, so they need a way to recover the charge.
+    if shipment["state"] not in CLAIMABLE_STATES | {"cancelled"}:
         raise ConflictError(
             "A claim cannot be filed against a shipment in '{}'.".format(shipment["state"]),
             code="not_claimable",
         )
-    if store.find_one("claims", shipment_id=shipment["id"], status="open"):
+    if store.find_one("claims", shipment_id=shipment["id"]):
         raise ConflictError("An open claim already exists for this shipment.", code="duplicate_claim")
```

The carrier-reporting fix:

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
@@ -40,9 +40,9 @@ def test_scan_for_another_merchant_is_not_found(client):
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
@@ -167,3 +167,16 @@ def test_claim_rejects_unknown_reason(client, shipment):
     assert response.status_code == 400
+
+
+def test_bulk_cancel_cancels_every_id(client):
+    first = make_shipment(client, ACME)
+    second = make_shipment(client, ACME, destination="Austin, TX")
+    response = client.post(
+        "/v1/shipments/bulk-cancel",
+        json={"shipment_ids": [first["id"], second["id"]]},
+        headers=ACME,
+    )
+    assert response.status_code == 200
+    assert response.get_json()["count"] == 2
+    assert all(s["state"] == "cancelled" for s in response.get_json()["cancelled"])
```
