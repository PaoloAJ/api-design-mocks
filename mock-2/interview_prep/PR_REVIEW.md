# PR #217 — Add account-to-account transfers

**Author:** @dev-jamie  **Reviewers:** you  **Branch:** `feature/transfers` → `main`

## Ticket: LEDGER-88: Transfers between accounts

> Customers want to move money between their accounts at the bank.
>
> 1. `POST /transfers` moves an amount from one account to another.
> 2. Both accounts' **balances** and **transaction histories** must show the transfer.
> 3. `GET /transfers/<id>` returns a single transfer.
> 4. `GET /accounts/<id>/transfers` lists every transfer that **involves** the account (sent **and** received). It's paginated with `page` (starting at 1) and `page_size` (default 20, max 100).
> 5. The mobile app retries on timeouts. Retrying with the same `idempotency_key` must **never** move money twice.
> 6. v1 only allows transfers between accounts in the **same currency**. Frozen or closed accounts can't send **or** receive.
> 7. Error responses follow the existing `{"error": "..."}` format and status-code conventions.

## Description

This adds transfers. A transfer writes a `transfer_out` transaction on the source account and a `transfer_in` transaction on the destination, then saves a `Transfer` record. It also adds a transfers blueprint with create, fetch, and a paginated list, and updates `balance()` to count transfers.

Tests added and passing ✅ (3 passed)

## Diff

```diff
diff --git a/api/transfers.py b/api/transfers.py
new file mode 100644
index 0000000..f2bfe8f
--- /dev/null
+++ b/api/transfers.py
@@ -0,0 +1,43 @@
+import math
+
+from flask import Blueprint, current_app, jsonify, request
+
+transfers_bp = Blueprint("transfers", __name__)
+
+
+def transfers():
+    return current_app.config["TRANSFERS"]
+
+
+@transfers_bp.post("/transfers")
+def create_transfer():
+    payload = request.get_json(silent=True) or {}
+    transfer = transfers().create_transfer(payload)
+    return jsonify(transfer.to_dict()), 200
+
+
+@transfers_bp.get("/transfers/<transfer_id>")
+def get_transfer(transfer_id):
+    transfer = transfers().store.transfers[transfer_id]
+    return jsonify(transfer.to_dict())
+
+
+@transfers_bp.get("/accounts/<account_id>/transfers")
+def list_account_transfers(account_id):
+    page = int(request.args.get("page", 1))
+    page_size = int(request.args.get("page_size", 20))
+
+    items = transfers().list_for_account(account_id)
+    total_pages = math.ceil(len(items) / page_size)
+    start = page * page_size
+    chunk = items[start:start + page_size]
+
+    return jsonify(
+        {
+            "account_id": account_id,
+            "page": page,
+            "page_size": page_size,
+            "total_pages": total_pages,
+            "transfers": [t.to_dict() for t in chunk],
+        }
+    )
diff --git a/app.py b/app.py
index 534ccc0..8d50a73 100644
--- a/app.py
+++ b/app.py
@@ -2,7 +2,9 @@ from flask import Flask, jsonify
 
 from api.accounts import accounts_bp
 from api.transactions import transactions_bp
+from api.transfers import transfers_bp
 from services.ledger import Ledger, LedgerError
+from services.transfers import TransferService
 from storage.csv_store import CsvStore
 
 
@@ -11,9 +13,11 @@ def create_app(data_dir=None):
 
     store = CsvStore(data_dir) if data_dir else CsvStore()
     app.config["LEDGER"] = Ledger(store)
+    app.config["TRANSFERS"] = TransferService(app.config["LEDGER"])
 
     app.register_blueprint(accounts_bp)
     app.register_blueprint(transactions_bp)
+    app.register_blueprint(transfers_bp)
 
     @app.errorhandler(LedgerError)
     def handle_ledger_error(error):
diff --git a/models/models.py b/models/models.py
index d083b61..7d8a723 100644
--- a/models/models.py
+++ b/models/models.py
@@ -3,7 +3,7 @@ from decimal import Decimal
 
 ACCOUNT_TYPES = {"checking", "savings", "brokerage"}
 ACCOUNT_STATUSES = {"active", "frozen", "closed"}
-TRANSACTION_TYPES = {"deposit", "withdrawal"}
+TRANSACTION_TYPES = {"deposit", "withdrawal", "transfer_in", "transfer_out"}
 
 
 @dataclass
@@ -32,3 +32,19 @@ class Transaction:
         data = asdict(self)
         data["amount"] = str(self.amount)
         return data
+
+
+@dataclass
+class Transfer:
+    id: str
+    from_account_id: str
+    to_account_id: str
+    amount: Decimal
+    created_at: str
+    idempotency_key: str = ""
+    memo: str = ""
+
+    def to_dict(self):
+        data = asdict(self)
+        data["amount"] = float(self.amount)
+        return data
diff --git a/services/ledger.py b/services/ledger.py
index e9cd919..758939a 100644
--- a/services/ledger.py
+++ b/services/ledger.py
@@ -66,7 +66,7 @@ class Ledger:
         self.get_account(account_id)
         total = Decimal("0.00")
         for transaction in self.store.transactions_for(account_id):
-            if transaction.type == "deposit":
+            if transaction.type in ("deposit", "transfer_in"):
                 total += transaction.amount
             elif transaction.type == "withdrawal":
                 total -= transaction.amount
diff --git a/services/transfers.py b/services/transfers.py
new file mode 100644
index 0000000..2d8d0ba
--- /dev/null
+++ b/services/transfers.py
@@ -0,0 +1,61 @@
+from datetime import datetime, timezone
+
+from models.models import Transaction, Transfer
+from services.ledger import ConflictError, UnprocessableError
+
+
+class TransferService:
+    def __init__(self, ledger):
+        self.ledger = ledger
+        self.store = ledger.store
+
+    def create_transfer(self, payload):
+        from_id = payload.get("from_account_id")
+        to_id = payload.get("to_account_id")
+        amount = self.ledger.parse_amount(payload.get("amount"))
+        now = datetime.now(timezone.utc).isoformat()
+
+        source = self.ledger.get_account(from_id)
+        if source.status != "active":
+            raise ConflictError(f"account {from_id} is {source.status}")
+
+        if amount >= self.ledger.balance(from_id):
+            raise UnprocessableError("insufficient funds")
+
+        debit = Transaction(
+            id=self.store.next_transaction_id(),
+            account_id=from_id,
+            type="transfer_out",
+            amount=amount,
+            created_at=now,
+            description=f"Transfer to {to_id}",
+        )
+        self.store.add_transaction(debit)
+
+        destination = self.ledger.get_account(to_id)
+
+        credit = Transaction(
+            id=self.store.next_transaction_id(),
+            account_id=destination.id,
+            type="transfer_in",
+            amount=amount,
+            created_at=now,
+            description=f"Transfer from {from_id}",
+        )
+        self.store.add_transaction(credit)
+
+        transfer = Transfer(
+            id=self.store.next_transfer_id(),
+            from_account_id=from_id,
+            to_account_id=to_id,
+            amount=amount,
+            created_at=now,
+            idempotency_key=payload.get("idempotency_key", ""),
+            memo=payload.get("memo", ""),
+        )
+        return self.store.add_transfer(transfer)
+
+    def list_for_account(self, account_id):
+        self.ledger.get_account(account_id)
+        items = self.store.transfers_for(account_id)
+        return sorted(items, key=lambda t: t.created_at, reverse=True)
diff --git a/storage/csv_store.py b/storage/csv_store.py
index 96b24b5..a5a2d56 100644
--- a/storage/csv_store.py
+++ b/storage/csv_store.py
@@ -12,6 +12,7 @@ class CsvStore:
         self.data_dir = data_dir
         self.accounts = {}
         self.transactions = []
+        self.transfers = {}
         self._load()
 
     def _read(self, filename):
@@ -35,6 +36,9 @@ class CsvStore:
     def next_transaction_id(self):
         return f"txn_{len(self.transactions) + 1:04d}"
 
+    def next_transfer_id(self):
+        return f"trf_{len(self.transfers) + 1:04d}"
+
     def get_account(self, account_id):
         return self.accounts.get(account_id)
 
@@ -54,3 +58,10 @@ class CsvStore:
 
     def transactions_for(self, account_id):
         return [t for t in self.transactions if t.account_id == account_id]
+
+    def add_transfer(self, transfer):
+        self.transfers[transfer.id] = transfer
+        return transfer
+
+    def transfers_for(self, account_id):
+        return [t for t in self.transfers.values() if t.from_account_id == account_id]
diff --git a/tests/test_transfers.py b/tests/test_transfers.py
new file mode 100644
index 0000000..2b43840
--- /dev/null
+++ b/tests/test_transfers.py
@@ -0,0 +1,54 @@
+from decimal import Decimal
+
+from app import create_app
+
+
+def make_client():
+    return create_app().test_client()
+
+
+def test_transfer_moves_money():
+    client = make_client()
+    before = client.get("/accounts/acc_0002").get_json()["balance"]
+
+    response = client.post(
+        "/transfers",
+        json={
+            "from_account_id": "acc_0001",
+            "to_account_id": "acc_0002",
+            "amount": "100.00",
+            "idempotency_key": "abc-123",
+        },
+    )
+
+    assert response.status_code == 200
+    after = client.get("/accounts/acc_0002").get_json()["balance"]
+    assert Decimal(after) == Decimal(before) + Decimal("100.00")
+
+
+def test_transfer_insufficient_funds():
+    client = make_client()
+    response = client.post(
+        "/transfers",
+        json={
+            "from_account_id": "acc_0003",
+            "to_account_id": "acc_0001",
+            "amount": "999999.00",
+        },
+    )
+    assert response.status_code == 422
+
+
+def test_list_transfers():
+    client = make_client()
+    client.post(
+        "/transfers",
+        json={
+            "from_account_id": "acc_0001",
+            "to_account_id": "acc_0002",
+            "amount": "25.00",
+        },
+    )
+    response = client.get("/accounts/acc_0001/transfers")
+    assert response.status_code == 200
+    assert "transfers" in response.get_json()
```
