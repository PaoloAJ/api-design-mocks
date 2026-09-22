import csv
import os
from decimal import Decimal

from models.models import Account, Transaction

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class CsvStore:
    def __init__(self, data_dir=DATA_DIR):
        self.data_dir = data_dir
        self.accounts = {}
        self.transactions = []
        self._load()

    def _read(self, filename):
        path = os.path.join(self.data_dir, filename)
        if not os.path.exists(path):
            return []
        with open(path, newline="") as f:
            return list(csv.DictReader(f))

    def _load(self):
        for row in self._read("accounts.csv"):
            account = Account(**row)
            self.accounts[account.id] = account
        for row in self._read("transactions.csv"):
            row["amount"] = Decimal(row["amount"])
            self.transactions.append(Transaction(**row))

    def next_account_id(self):
        return f"acc_{len(self.accounts) + 1:04d}"

    def next_transaction_id(self):
        return f"txn_{len(self.transactions) + 1:04d}"

    def get_account(self, account_id):
        return self.accounts.get(account_id)

    def list_accounts(self, owner=None):
        accounts = list(self.accounts.values())
        if owner:
            accounts = [a for a in accounts if a.owner == owner]
        return accounts

    def add_account(self, account):
        self.accounts[account.id] = account
        return account

    def add_transaction(self, transaction):
        self.transactions.append(transaction)
        return transaction

    def transactions_for(self, account_id):
        return [t for t in self.transactions if t.account_id == account_id]
