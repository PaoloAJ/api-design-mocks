from flask import Flask, jsonify

from api.accounts import accounts_bp
from api.transactions import transactions_bp
from services.ledger import Ledger, LedgerError
from storage.csv_store import CsvStore


def create_app(data_dir=None):
    app = Flask(__name__)

    store = CsvStore(data_dir) if data_dir else CsvStore()
    app.config["LEDGER"] = Ledger(store)

    app.register_blueprint(accounts_bp)
    app.register_blueprint(transactions_bp)

    @app.errorhandler(LedgerError)
    def handle_ledger_error(error):
        return jsonify({"error": error.message}), error.status_code

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5000)
