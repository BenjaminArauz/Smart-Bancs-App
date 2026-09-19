"""
Composición de dependencias.

Este es el ÚNICO lugar donde se conectan las capas: se instancian los
adaptadores concretos (SQLAlchemy) y se inyectan en el caso de uso a
través de sus puertos (Protocols). En un test, esta función se
reemplaza por una que arma el caso de uso con fakes en memoria —
sin tocar el resto del código.
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_session
from app.infrastructure.repositories import (
    SqlAccountRepository,
    SqlTransactionRepository,
    SqlOutboxRepository,
    SqlAlchemyUnitOfWork,
)
from app.application.use_cases import ProcessTransactionUseCase
from app.core.config import settings


def get_process_transaction_use_case(session: AsyncSession = Depends(get_session)) -> ProcessTransactionUseCase:
    return ProcessTransactionUseCase(
        account_repo=SqlAccountRepository(session),
        transaction_repo=SqlTransactionRepository(session),
        outbox_repo=SqlOutboxRepository(session),
        uow=SqlAlchemyUnitOfWork(session),
        max_retries=settings.max_optimistic_retries,
    )