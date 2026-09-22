from flask import Blueprint, current_app, jsonify, request

transactions_bp = Blueprint(
    "transactions", __name__, url_prefix="/accounts/<account_id>/transactions"
)


def ledger():
    return current_app.config["LEDGER"]


@transactions_bp.get("")
def list_transactions(account_id):
    tx_type = request.args.get("type")
    items = ledger().history(account_id, tx_type)
    return jsonify(
        {
            "account_id": account_id,
            "count": len(items),
            "transactions": [t.to_dict() for t in items],
        }
    )


@transactions_bp.post("")
def create_transaction(account_id):
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "request body must be JSON"}), 400
    transaction = ledger().record_transaction(account_id, payload)
    return jsonify(transaction.to_dict()), 201
