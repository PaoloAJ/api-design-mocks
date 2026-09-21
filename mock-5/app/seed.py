"""Seed data so the API has something to show on first run."""

from __future__ import annotations

from app.core.store import store

SEED_PAYMENTS = [
    {
        "amount": 4999,
        "currency": "USD",
        "customer_id": "cus_alpha_amir",
        "description": "Annual subscription — Pro plan",
        "metadata": {"plan": "pro", "cycle": "annual"},
        "status": "captured",
        "amount_refunded": 0,
        "captured_at": "2026-09-10T14:02:11.000Z",
        "voided_at": None,
        "settled_at": None,
        "settlement_reference": None,
        "org_id": "merchant_alpha",
    },
    {
        "amount": 12000,
        "currency": "USD",
        "customer_id": "cus_alpha_bree",
        "description": "Onboarding services",
        "metadata": {},
        "status": "settled",
        "amount_refunded": 2000,
        "captured_at": "2026-09-01T09:15:00.000Z",
        "voided_at": None,
        "settled_at": "2026-09-03T00:00:00.000Z",
        "settlement_reference": "batch_20260903_01",
        "org_id": "merchant_alpha",
    },
    {
        "amount": 1500,
        "currency": "EUR",
        "customer_id": "cus_alpha_chen",
        "description": "Add-on seats",
        "metadata": {"seats": "3"},
        "status": "authorized",
        "amount_refunded": 0,
        "captured_at": None,
        "voided_at": None,
        "settled_at": None,
        "settlement_reference": None,
        "org_id": "merchant_alpha",
    },
    {
        "amount": 7500,
        "currency": "GBP",
        "customer_id": "cus_beta_dana",
        "description": "Consulting retainer",
        "metadata": {},
        "status": "captured",
        "amount_refunded": 0,
        "captured_at": "2026-09-08T11:40:00.000Z",
        "voided_at": None,
        "settled_at": None,
        "settlement_reference": None,
        "org_id": "merchant_beta",
    },
]


def seed():
    """Idempotent: safe to call on every boot."""
    if store.list("payments"):
        return
    for payload in SEED_PAYMENTS:
        payment = store.create("payments", dict(payload))
        if payment["amount_refunded"] > 0:
            store.create(
                "refunds",
                {
                    "payment_id": payment["id"],
                    "org_id": payment["org_id"],
                    "amount": payment["amount_refunded"],
                    "reason": "Customer requested partial refund",
                    "status": "succeeded",
                },
            )
