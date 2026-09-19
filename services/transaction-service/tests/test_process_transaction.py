"""
Tests del caso de uso ProcessTransactionUseCase.

Los fakes de abajo implementan los mismos Protocols que los
adaptadores de SQLAlchemy (app/application/ports.py), pero en
memoria pura. Esto es lo que gana la arquitectura hexagonal en la
práctica: estos tests corren en milisegundos, sin Postgres, sin
Docker, y prueban exactamente la lógica que más riesgo tiene
(condiciones de carrera, duplicados) de forma determinística.
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities import Account
from app.domain.exceptions import InsufficientFundsError, ConcurrencyConflictError
from app.application.dto import ProcessTransactionCommand
from app.application.use_cases import ProcessTransactionUseCase


class FakeAccountRepository:
    def __init__(self, account: Account, fail_updates_until_attempt: int = 0):
        self._account = account
        self._attempts = 0
        self._fail_updates_until_attempt = fail_updates_until_attempt

    async def get(self, account_id):
        return self._account

    async def update_with_version_check(self, updated, expected_version):
        self._attempts += 1
        if self._attempts <= self._fail_updates_until_attempt:
            return False  # simula que otra transacción ganó la carrera
        self._account = updated
        return True


class FakeTransactionRepository:
    def __init__(self):
        self._by_key: dict[str, object] = {}

    async def find_by_idempotency_key(self, key):
        return self._by_key.get(key)

    def add(self, transaction):
        self._by_key[transaction.idempotency_key] = transaction


class FakeOutboxRepository:
    def __init__(self):
        self.events = []

    def add_many(self, events):
        self.events.extend(events)


class FakeUnitOfWork:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def build_use_case(account: Account, fail_updates_until_attempt: int = 0):
    account_repo = FakeAccountRepository(account, fail_updates_until_attempt)
    transaction_repo = FakeTransactionRepository()
    outbox_repo = FakeOutboxRepository()
    uow = FakeUnitOfWork()
    use_case = ProcessTransactionUseCase(account_repo, transaction_repo, outbox_repo, uow, max_retries=3)
    return use_case, transaction_repo, outbox_repo, uow


@pytest.mark.asyncio
async def test_successful_transaction_debits_account_and_writes_outbox_events():
    account = Account(id=uuid4(), balance=Decimal("1000.00"), version=0)
    use_case, _, outbox_repo, uow = build_use_case(account)

    command = ProcessTransactionCommand(
        idempotency_key="key-1", account_id=account.id, amount=Decimal("150.00"), trace_id="trace-1"
    )
    result = await use_case.execute(command)

    assert result.new_balance == Decimal("850.00")
    assert result.was_replayed is False
    assert len(outbox_repo.events) == 2  # bancs.sync + ai.recommend
    assert uow.commits == 1


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_does_not_double_charge():
    account = Account(id=uuid4(), balance=Decimal("1000.00"), version=0)
    use_case, transaction_repo, _, _ = build_use_case(account)

    command = ProcessTransactionCommand(
        idempotency_key="key-1", account_id=account.id, amount=Decimal("150.00"), trace_id="trace-1"
    )
    first = await use_case.execute(command)
    second = await use_case.execute(command)

    assert second.was_replayed is True
    assert second.transaction_id == first.transaction_id


@pytest.mark.asyncio
async def test_insufficient_funds_raises_domain_error():
    account = Account(id=uuid4(), balance=Decimal("10.00"), version=0)
    use_case, *_ = build_use_case(account)

    command = ProcessTransactionCommand(
        idempotency_key="key-2", account_id=account.id, amount=Decimal("500.00"), trace_id="trace-2"
    )
    with pytest.raises(InsufficientFundsError):
        await use_case.execute(command)


@pytest.mark.asyncio
async def test_retries_on_version_conflict_and_eventually_succeeds():
    account = Account(id=uuid4(), balance=Decimal("1000.00"), version=0)
    # simula que las primeras 2 escrituras pierden la carrera de concurrencia
    use_case, *_ = build_use_case(account, fail_updates_until_attempt=2)

    command = ProcessTransactionCommand(
        idempotency_key="key-3", account_id=account.id, amount=Decimal("100.00"), trace_id="trace-3"
    )
    result = await use_case.execute(command)

    assert result.new_balance == Decimal("900.00")


@pytest.mark.asyncio
async def test_exhausting_retries_raises_concurrency_conflict():
    account = Account(id=uuid4(), balance=Decimal("1000.00"), version=0)
    # falla siempre: nunca gana la carrera dentro del límite de reintentos
    use_case, *_ = build_use_case(account, fail_updates_until_attempt=99)

    command = ProcessTransactionCommand(
        idempotency_key="key-4", account_id=account.id, amount=Decimal("100.00"), trace_id="trace-4"
    )
    with pytest.raises(ConcurrencyConflictError):
        await use_case.execute(command)