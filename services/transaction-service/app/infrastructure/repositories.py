"""
Adaptadores de persistencia (implementan los Protocols de application/ports.py).

Esta es la ÚNICA capa que sabe que existe SQLAlchemy/Postgres. Si
mañana cambian a otra base de datos, esto es lo único que se
reescribe — el caso de uso y el dominio quedan intactos.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import Account, Transaction, OutboxEvent
from app.infrastructure.orm_models import AccountORM, TransactionORM, OutboxEventORM


class SqlAccountRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, account_id: UUID) -> Account | None:
        row = await self._session.get(AccountORM, account_id)
        if row is None:
            return None
        return Account(id=row.id, balance=Decimal(row.balance), version=row.version)

    async def update_with_version_check(self, updated: Account, expected_version: int) -> bool:
        result = await self._session.execute(
            update(AccountORM)
            .where(AccountORM.id == updated.id, AccountORM.version == expected_version)
            .values(balance=updated.balance, version=updated.version)
        )
        return result.rowcount == 1


class SqlTransactionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def find_by_idempotency_key(self, key: str) -> Transaction | None:
        row = await self._session.scalar(select(TransactionORM).where(TransactionORM.idempotency_key == key))
        if row is None:
            return None
        return Transaction(
            id=row.id,
            idempotency_key=row.idempotency_key,
            account_id=row.account_id,
            amount=Decimal(row.amount),
            status=row.status,
            trace_id=row.trace_id,
        )

    def add(self, transaction: Transaction) -> None:
        self._session.add(
            TransactionORM(
                id=transaction.id,
                idempotency_key=transaction.idempotency_key,
                account_id=transaction.account_id,
                amount=transaction.amount,
                status=transaction.status,
                trace_id=transaction.trace_id,
            )
        )


class SqlOutboxRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    def add_many(self, events: list[OutboxEvent]) -> None:
        self._session.add_all(
            [OutboxEventORM(id=e.id, event_type=e.event_type, payload=e.payload, trace_id=e.trace_id) for e in events]
        )


class SqlAlchemyUnitOfWork:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
