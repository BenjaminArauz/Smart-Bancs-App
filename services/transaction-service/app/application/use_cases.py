"""
Caso de uso: ProcessTransactionUseCase.

Aquí vive TODA la regla de negocio de la sección 3.1 del reto:
idempotencia, locking optimista con reintentos, y outbox pattern.
No importa FastAPI, no importa SQLAlchemy, no importa HTTP. Se puede
testear instanciando fakes en memoria (ver tests/test_transactions.py)
y correr en milisegundos, sin BD real.
"""

import uuid

from app.core.logging import get_logger
from app.domain.entities import Account, Transaction, OutboxEvent, TransactionStatus
from app.domain.exceptions import AccountNotFoundError, InsufficientFundsError, ConcurrencyConflictError
from app.application.dto import ProcessTransactionCommand, TransactionResult
from app.application.ports import AccountRepository, TransactionRepository, OutboxRepository, UnitOfWork

logger = get_logger(__name__)


class ProcessTransactionUseCase:
    def __init__(
        self,
        account_repo: AccountRepository,
        transaction_repo: TransactionRepository,
        outbox_repo: OutboxRepository,
        uow: UnitOfWork,
        max_retries: int = 3,
    ):
        self._accounts = account_repo
        self._transactions = transaction_repo
        self._outbox = outbox_repo
        self._uow = uow
        self._max_retries = max_retries

    async def execute(self, command: ProcessTransactionCommand) -> TransactionResult:
        # 1. Idempotencia: si ya procesamos esta petición, no la repetimos.
        existing = await self._transactions.find_by_idempotency_key(command.idempotency_key)
        if existing is not None:
            logger.info(f"replay idempotente para idempotency_key={command.idempotency_key}")
            account = await self._accounts.get(existing.account_id)
            return TransactionResult(
                transaction_id=existing.id,
                status=existing.status.value,
                new_balance=account.balance,
                trace_id=existing.trace_id,
                was_replayed=True,
            )

        # 2. Locking optimista con reintentos acotados.
        account = None
        for attempt in range(1, self._max_retries + 1):
            account = await self._accounts.get(command.account_id)
            if account is None:
                raise AccountNotFoundError(command.account_id)
            if not account.has_sufficient_funds(command.amount):
                raise InsufficientFundsError(command.account_id, account.balance, command.amount)

            updated_account = account.debit(command.amount)
            applied = await self._accounts.update_with_version_check(updated_account, expected_version=account.version)

            if applied:
                account = updated_account
                break

            logger.warning(f"conflicto de versión, intento {attempt}/{self._max_retries}")
            await self._uow.rollback()
            if attempt == self._max_retries:
                raise ConcurrencyConflictError(command.account_id)

        # 3. Persistir transacción + eventos outbox de forma atómica.
        transaction = Transaction(
            id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
            account_id=account.id,
            amount=command.amount,
            status=TransactionStatus.COMPLETED,
            trace_id=command.trace_id,
        )
        self._transactions.add(transaction)

        self._outbox.add_many([
            OutboxEvent(
                id=uuid.uuid4(),
                event_type="bancs.sync",
                payload={"account_id": str(account.id), "amount": str(command.amount), "transaction_id": str(transaction.id)},
                trace_id=command.trace_id,
            ),
            OutboxEvent(
                id=uuid.uuid4(),
                event_type="ai.recommend",
                payload={"account_id": str(account.id), "transaction_id": str(transaction.id)},
                trace_id=command.trace_id,
            ),
        ])

        await self._uow.commit()
        logger.info(f"transacción {transaction.id} completada")

        return TransactionResult(
            transaction_id=transaction.id,
            status=transaction.status.value,
            new_balance=account.balance,
            trace_id=command.trace_id,
            was_replayed=False,
        )