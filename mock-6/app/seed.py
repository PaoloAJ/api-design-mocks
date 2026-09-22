"""Sample data, so the app is worth looking at on first run.

Not used by tests — they start from an empty store.
"""

from __future__ import annotations

from app.core.store import store

BUILDS = [
    ("web", "prj_web", "main", "a1b2c3d4e5f6", "push", "passed", ["build", "test"]),
    ("web", "prj_web", "main", "b2c3d4e5f6a1", "push", "failed", ["build", "test"]),
    ("web", "prj_web", "feat/login", "c3d4e5f6a1b2", "pull_request", "running",
     ["build", "test", "lint"]),
    ("api", "prj_api", "main", "d4e5f6a1b2c3", "schedule", "passed", ["build"]),
    ("api", "prj_api", "fix/timeout", "e5f6a1b2c3d4", "pull_request", "queued",
     ["build", "test"]),
]


def seed():
    for slug, project_id, branch, commit, trigger, state, jobs in BUILDS:
        build = store.create(
            "builds",
            {
                "project_id": project_id,
                "project_slug": slug,
                "branch": branch,
                "commit": commit,
                "trigger": trigger,
                "state": state,
                "jobs": jobs,
                "started_at": None,
                "finished_at": None,
            },
        )
        if state in ("running", "passed", "failed"):
            for name in jobs:
                store.create(
                    "jobs",
                    {
                        "build_id": build["id"],
                        "project_id": project_id,
                        "project_slug": slug,
                        "name": name,
                        "state": "running" if state == "running" else state,
                        "finished_at": None,
                    },
                )
