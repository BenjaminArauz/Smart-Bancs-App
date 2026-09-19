"""
Puertos de salida (el lado "driven" del hexágono).

Son Protocols (structural typing de Python), no ABCs con herencia.
Ventaja práctica: en los tests puedo pasar cualquier objeto que
"tenga la forma correcta" (un fake en memoria) sin heredar de nada
ni depender de SQLAlchemy. El caso de uso solo conoce estas
interfaces — nunca importa `infrastructure/`.
"""

from typing import Protocol, Optional
from uuid import UUID

from app.domain.entities import Account, Transaction, OutboxEvent


class AccountRepository(Protocol):
    async def get(self, account_id: UUID) -> Optional[Account]: ...

    async def update_with_version_check(self, updated: Account, expected_version: int) -> bool:
        """Devuelve True si el update tuvo efecto (nadie más modificó la cuenta)."""
        ...


class TransactionRepository(Protocol):
    async def find_by_idempotency_key(self, key: str) -> Optional[Transaction]: ...

    def add(self, transaction: Transaction) -> None: ...


class OutboxRepository(Protocol):
    def add_many(self, events: list[OutboxEvent]) -> None: ...


class UnitOfWork(Protocol):
    """Abstrae el commit/rollback para que el caso de uso no dependa de SQLAlchemy."""

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...