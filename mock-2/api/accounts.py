from flask import Blueprint, current_app, jsonify, request

accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")


def ledger():
    return current_app.config["LEDGER"]


@accounts_bp.get("")
def list_accounts():
    owner = request.args.get("owner")
    accounts = ledger().store.list_accounts(owner)
    return jsonify([account.to_dict() for account in accounts])


@accounts_bp.post("")
def open_account():
    payload = request.get_json(silent=True) or {}
    account = ledger().open_account(payload)
    return jsonify(account.to_dict()), 201


@accounts_bp.get("/<account_id>")
def get_account(account_id):
    account = ledger().get_account(account_id)
    data = account.to_dict()
    data["balance"] = str(ledger().balance(account_id))
    return jsonify(data)
