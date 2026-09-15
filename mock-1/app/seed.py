"""Seed data so the API has something to show on first run."""

from __future__ import annotations

from app.core.store import store

SEED_MONITORS = [
    {
        "name": "High API latency",
        "type": "metric",
        "query": "avg(last_5m):avg:api.request.duration{env:prod} > 500",
        "thresholds": {"warning": 300, "critical": 500},
        "tags": ["env:prod", "team:api", "service:gateway"],
        "enabled": True,
        "status": "alert",
        "org_id": "org_alpha",
    },
    {
        "name": "Error rate spike",
        "type": "log",
        "query": "logs('status:error service:checkout').index('main').rollup('count')",
        "thresholds": {"warning": 10, "critical": 50},
        "tags": ["env:prod", "team:checkout"],
        "enabled": True,
        "status": "warn",
        "org_id": "org_alpha",
    },
    {
        "name": "Checkout synthetic test",
        "type": "synthetic",
        "query": "synthetics('checkout-flow').status()",
        "thresholds": {"warning": None, "critical": 1},
        "tags": ["env:prod", "team:checkout"],
        "enabled": False,
        "status": "ok",
        "org_id": "org_alpha",
    },
    {
        "name": "Staging DB connections",
        "type": "metric",
        "query": "max(last_10m):max:postgres.connections{env:staging} > 90",
        "thresholds": {"warning": 70, "critical": 90},
        "tags": ["env:staging", "team:platform"],
        "enabled": True,
        "status": "ok",
        "org_id": "org_beta",
    },
]


def seed():
    """Idempotent: safe to call on every boot."""
    if store.list("monitors"):
        return
    for payload in SEED_MONITORS:
        monitor = store.create("monitors", dict(payload))
        if monitor["status"] in ("warn", "alert"):
            store.create(
                "alerts",
                {
                    "monitor_id": monitor["id"],
                    "org_id": monitor["org_id"],
                    "severity": "critical" if monitor["status"] == "alert" else "warning",
                    "state": "open",
                    "message": "Monitor '{}' is in {} state".format(
                        monitor["name"], monitor["status"]
                    ),
                    "resolved_at": None,
                },
            )
