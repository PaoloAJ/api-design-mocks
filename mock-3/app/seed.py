"""Seed data so the API has something to show on first run."""

from __future__ import annotations

from app.core.store import store

SEED_CASES = [
    {
        "external_id": "post_10021",
        "kind": "post",
        "text": "Buy cheap followers now at bit.ly/notreal — limited offer!!",
        "reason": "spam",
        "queue": "priority",
        "labels": ["lang:en", "classifier:spam"],
        "state": "pending",
        "score": 0.94,
        "decided_by": None,
        "decided_at": None,
        "platform_id": "plat_alpha",
    },
    {
        "external_id": "comment_55180",
        "kind": "comment",
        "text": "You are worthless and everyone here knows it.",
        "reason": "harassment",
        "queue": "priority",
        "labels": ["lang:en", "classifier:harassment"],
        "state": "in_review",
        "score": 0.81,
        "decided_by": None,
        "decided_at": None,
        "platform_id": "plat_alpha",
    },
    {
        "external_id": "image_7741",
        "kind": "image",
        "text": "[image] beach photo flagged by user report",
        "reason": "nudity",
        "queue": "standard",
        "labels": ["classifier:nudity", "confidence:low"],
        "state": "removed",
        "score": 0.52,
        "decided_by": "reviewer_kim",
        "decided_at": "2026-09-14T11:02:00.000Z",
        "platform_id": "plat_alpha",
    },
    {
        "external_id": "post_31004",
        "kind": "post",
        "text": "Detailed instructions for a dangerous prank.",
        "reason": "violence",
        "queue": "legal",
        "labels": ["lang:de", "classifier:violence"],
        "state": "pending",
        "score": 0.77,
        "decided_by": None,
        "decided_at": None,
        "platform_id": "plat_beta",
    },
]


def seed():
    """Idempotent: safe to call on every boot."""
    if store.list("cases"):
        return
    for payload in SEED_CASES:
        case = store.create("cases", dict(payload))
        if case["state"] == "removed":
            store.create(
                "appeals",
                {
                    "case_id": case["id"],
                    "platform_id": case["platform_id"],
                    "state": "open",
                    "submitted_by": "user_4820",
                    "statement": "This is a family photo at a public beach.",
                    "resolved_by": None,
                    "resolved_at": None,
                },
            )
