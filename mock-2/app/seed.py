"""Seed data so the API has something to show on first run."""

from __future__ import annotations

from app.core.store import store

SEED_SERVICES = [
    {"name": "checkout", "tier": 1, "owner_team": "team-checkout", "org_id": "org_alpha"},
    {"name": "api-gateway", "tier": 1, "owner_team": "team-api", "org_id": "org_alpha"},
    {"name": "recommendations", "tier": 3, "owner_team": "team-ml", "org_id": "org_alpha"},
    {"name": "billing", "tier": 2, "owner_team": "team-billing", "org_id": "org_beta"},
]


def seed():
    """Idempotent: safe to call on every boot."""
    if store.list("services"):
        return

    services = {}
    for payload in SEED_SERVICES:
        service = store.create("services", dict(payload))
        services[(service["org_id"], service["name"])] = service

    checkout = services[("org_alpha", "checkout")]
    gateway = services[("org_alpha", "api-gateway")]
    recs = services[("org_alpha", "recommendations")]
    billing = services[("org_beta", "billing")]

    seed_incidents = [
        {
            "title": "Checkout 500s spiking",
            "summary": "Elevated 5xx on /checkout since ~14:02 UTC.",
            "severity": "sev1",
            "status": "investigating",
            "service_id": checkout["id"],
            "org_id": "org_alpha",
            "commander": "priya",
            "responders": ["priya", "sam"],
            "tags": ["env:prod"],
            "resolved_at": None,
        },
        {
            "title": "Gateway latency above SLO",
            "summary": "p99 latency crossed 800ms for 10 minutes.",
            "severity": "sev3",
            "status": "acknowledged",
            "service_id": gateway["id"],
            "org_id": "org_alpha",
            "commander": "sam",
            "responders": ["sam"],
            "tags": ["env:prod", "team:api"],
            "resolved_at": None,
        },
        {
            "title": "Stale recommendation cache",
            "summary": "Cache TTL bug served day-old results; fixed and verified.",
            "severity": "sev4",
            "status": "resolved",
            "service_id": recs["id"],
            "org_id": "org_alpha",
            "commander": None,
            "responders": ["jae"],
            "tags": ["env:prod"],
            "resolved_at": "2026-08-01T10:00:00.000Z",
        },
        {
            "title": "Billing webhook backlog",
            "summary": "Retry storm from a downstream 500 backed up the queue.",
            "severity": "sev2",
            "status": "monitoring",
            "service_id": billing["id"],
            "org_id": "org_beta",
            "commander": "devon",
            "responders": ["devon"],
            "tags": ["env:prod"],
            "resolved_at": None,
        },
    ]

    for payload in seed_incidents:
        incident = store.create("incidents", dict(payload))
        store.create(
            "timeline_entries",
            {
                "incident_id": incident["id"],
                "org_id": incident["org_id"],
                "kind": "created",
                "author": "system",
                "message": "Incident created",
            },
        )
        if incident["status"] != "triggered":
            store.create(
                "timeline_entries",
                {
                    "incident_id": incident["id"],
                    "org_id": incident["org_id"],
                    "kind": "status_change",
                    "author": "system",
                    "message": "Status changed to {}".format(incident["status"]),
                },
            )
