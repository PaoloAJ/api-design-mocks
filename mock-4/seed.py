"""Demo data so the app is explorable the moment it starts.

Two merchants, so tenant isolation is visible by calling the same endpoint
with two different keys. Shipments are spread across the lifecycle so every
state has at least one example.
"""

from core.store import store

SHIPMENTS = [
    ("mer_acme", "Portland, OR", "ups", 2.4, "label_created", "PO-8841"),
    ("mer_acme", "Austin, TX", "fedex", 11.0, "in_transit", "PO-8842"),
    ("mer_acme", "Boston, MA", "usps", 0.8, "out_for_delivery", "PO-8843"),
    ("mer_acme", "Denver, CO", "ups", 5.2, "delivered", "PO-8844"),
    ("mer_acme", "Miami, FL", "dhl", 17.5, "exception", "PO-8845"),
    ("mer_globex", "Seattle, WA", "fedex", 3.1, "in_transit", "GX-201"),
    ("mer_globex", "Chicago, IL", "ups", 22.0, "delivered", "GX-202"),
]


def seed():
    if store.list("shipments"):
        return
    for merchant_id, destination, carrier, weight, state, reference in SHIPMENTS:
        shipment = store.create(
            "shipments",
            {
                "merchant_id": merchant_id,
                "destination": destination,
                "carrier": carrier,
                "weight_kg": weight,
                "state": state,
                "reference": reference,
                "delivered_at": None,
            },
        )
        if state == "delivered":
            store.update("shipments", shipment["id"], {"delivered_at": shipment["created_at"]})
