"""/v1/services — a small, read-only catalog.

Real Datadog has a Service Catalog that many teams write to via CI/CD
manifests, not by hand through an incident tool. Here it is seed-only and
read-only through the API on purpose: it is a good "why is there no POST
/v1/services" discussion — who should own this data, and would an
incident-response API really want to be its system of record?
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify

from app.core.errors import NotFoundError
from app.core.store import store

bp = Blueprint("services", __name__, url_prefix="/v1/services")


def serialize(service):
    return {
        "id": service["id"],
        "name": service["name"],
        "tier": service["tier"],
        "owner_team": service["owner_team"],
        "org_id": service["org_id"],
        "created_at": service["created_at"],
        "updated_at": service["updated_at"],
    }


@bp.get("")
def list_services():
    records = [s for s in store.list("services") if s["org_id"] == g.org_id]
    records = sorted(records, key=lambda s: s["name"])
    return jsonify({"data": [serialize(s) for s in records]})


@bp.get("/<service_id>")
def get_service(service_id):
    service = store.get("services", service_id)
    if service["org_id"] != g.org_id:
        # 404, not 403 — see app/api/incidents.py:_get_owned for why.
        raise NotFoundError("Service '{}' was not found.".format(service_id))
    return jsonify(serialize(service))
