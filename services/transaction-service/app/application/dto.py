"""
DTOs de la capa de aplicación.

Se ven parecidos a los schemas de Pydantic en app/api/v1/schemas.py,
pero son un objeto distinto a propósito: el contrato HTTP (schemas)
puede cambiar por razones de API (versionado, nombres de campos)
sin que eso obligue a tocar el caso de uso, y viceversa.
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class ProcessTransactionCommand:
    idempotency_key: str
    account_id: UUID
    amount: Decimal
    trace_id: str


@dataclass(frozen=True)
class TransactionResult:
    transaction_id: UUID
    status: str
    new_balance: Decimal
    trace_id: str
    was_replayed: bool