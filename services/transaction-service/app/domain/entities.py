"""
Entidades de dominio.

Deliberadamente NO son modelos de SQLAlchemy. El dominio no debe
saber que existe una base de datos, un ORM, o HTTP. Esto permite
testear las reglas de negocio (¿hay saldo suficiente? ¿la versión
coincide?) con objetos de Python puros, sin levantar Postgres.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID


class TransactionStatus(str, Enum):
    COMPLETED = "completed"


@dataclass
class Account:
    id: UUID
    balance: Decimal
    version: int

    def has_sufficient_funds(self, amount: Decimal) -> bool:
        return self.balance >= amount

    def debit(self, amount: Decimal) -> "Account":
        """Devuelve una nueva instancia con el saldo actualizado (inmutable)."""
        return Account(id=self.id, balance=self.balance - amount, version=self.version + 1)


@dataclass
class Transaction:
    id: UUID
    idempotency_key: str
    account_id: UUID
    amount: Decimal
    status: TransactionStatus
    trace_id: str


@dataclass
class OutboxEvent:
    id: UUID
    event_type: str
    payload: dict
    trace_id: str
    