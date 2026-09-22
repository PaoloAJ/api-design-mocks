from dataclasses import asdict, dataclass
from decimal import Decimal

ACCOUNT_TYPES = {"checking", "savings", "brokerage"}
ACCOUNT_STATUSES = {"active", "frozen", "closed"}
TRANSACTION_TYPES = {"deposit", "withdrawal"}


@dataclass
class Account:
    id: str
    owner: str
    account_type: str
    currency: str
    status: str
    created_at: str

    def to_dict(self):
        return asdict(self)


@dataclass
class Transaction:
    id: str
    account_id: str
    type: str
    amount: Decimal
    created_at: str
    description: str = ""

    def to_dict(self):
        data = asdict(self)
        data["amount"] = str(self.amount)
        return data
