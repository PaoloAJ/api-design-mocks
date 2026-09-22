from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from models.models import ACCOUNT_TYPES, TRANSACTION_TYPES, Account, Transaction

SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP"}
MAX_TRANSACTION_AMOUNT = Decimal("1000000.00")


class LedgerError(Exception):
    status_code = 400

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class NotFoundError(LedgerError):
    status_code = 404


class ConflictError(LedgerError):
    status_code = 409


class UnprocessableError(LedgerError):
    status_code = 422


class Ledger:
    def __init__(self, store):
        self.store = store

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def get_account(self, account_id):
        account = self.store.get_account(account_id)
        if account is None:
            raise NotFoundError(f"account {account_id} not found")
        return account

    def open_account(self, payload):
        owner = (payload.get("owner") or "").strip()
        account_type = payload.get("account_type")
        currency = payload.get("currency", "USD")

        if not owner:
            raise LedgerError("owner is required")
        if account_type not in ACCOUNT_TYPES:
            raise LedgerError(f"account_type must be one of {sorted(ACCOUNT_TYPES)}")
        if currency not in SUPPORTED_CURRENCIES:
            raise LedgerError(f"currency must be one of {sorted(SUPPORTED_CURRENCIES)}")

        account = Account(
            id=self.store.next_account_id(),
            owner=owner,
            account_type=account_type,
            currency=currency,
            status="active",
            created_at=self._now(),
        )
        return self.store.add_account(account)

    def balance(self, account_id):
        self.get_account(account_id)
        total = Decimal("0.00")
        for transaction in self.store.transactions_for(account_id):
            if transaction.type == "deposit":
                total += transaction.amount
            elif transaction.type == "withdrawal":
                total -= transaction.amount
        return total

    def parse_amount(self, raw):
        try:
            amount = Decimal(str(raw))
        except (InvalidOperation, TypeError):
            raise LedgerError("amount must be a number")
        if not amount.is_finite():
            raise LedgerError("amount must be a number")
        if amount <= 0:
            raise LedgerError("amount must be positive")
        if amount > MAX_TRANSACTION_AMOUNT:
            raise LedgerError(f"amount cannot exceed {MAX_TRANSACTION_AMOUNT}")
        if amount.as_tuple().exponent < -2:
            raise LedgerError("amount cannot have more than 2 decimal places")
        return amount

    def record_transaction(self, account_id, payload):
        account = self.get_account(account_id)
        if account.status != "active":
            raise ConflictError(f"account {account_id} is {account.status}")

        tx_type = payload.get("type")
        if tx_type not in TRANSACTION_TYPES:
            raise LedgerError(f"type must be one of {sorted(TRANSACTION_TYPES)}")

        amount = self.parse_amount(payload.get("amount"))

        if tx_type == "withdrawal" and amount > self.balance(account_id):
            raise UnprocessableError("insufficient funds")

        transaction = Transaction(
            id=self.store.next_transaction_id(),
            account_id=account_id,
            type=tx_type,
            amount=amount,
            created_at=self._now(),
            description=(payload.get("description") or "").strip(),
        )
        return self.store.add_transaction(transaction)

    def history(self, account_id, tx_type=None):
        self.get_account(account_id)
        items = self.store.transactions_for(account_id)
        if tx_type:
            items = [t for t in items if t.type == tx_type]
        return sorted(items, key=lambda t: t.created_at, reverse=True)
