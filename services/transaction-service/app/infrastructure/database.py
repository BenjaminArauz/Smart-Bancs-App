"""Engine y sesión de base de datos.

El pool está dimensionado para alta concurrencia: pool_timeout bajo
para fallar rápido (y que el circuit breaker / retry del cliente
actúe) en vez de encolar peticiones indefinidamente cuando la BD
está saturada. pool_pre_ping evita usar conexiones muertas.
"""

import time

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import db_errors_total, db_query_duration_seconds

logger = get_logger("database")


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_seconds,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def _operation(statement: str) -> str:
    first_word = statement.lstrip().split(maxsplit=1)[0].upper() if statement.strip() else "UNKNOWN"
    return first_word if first_word in {"SELECT", "INSERT", "UPDATE", "DELETE", "COMMIT", "ROLLBACK"} else "OTHER"


def _error_type(exception: BaseException) -> str:
    sqlstate = getattr(exception, "sqlstate", None) or getattr(exception, "pgcode", None)
    if type(exception).__name__ == "TimeoutError":
        return "pool_timeout"
    return {
        "40P01": "deadlock",
        "55P03": "lock_timeout",
        "57014": "statement_timeout",
    }.get(sqlstate, "database_error")


@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    context._smartbancs_query_started_at = time.perf_counter()


@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    elapsed = time.perf_counter() - context._smartbancs_query_started_at
    operation = _operation(statement)
    db_query_duration_seconds.labels(operation, "success").observe(elapsed)
    safe_statement = " ".join(statement.split()).replace('"', "'")[:300]
    logger.info("db query completed operation=%s duration_ms=%.1f sql=%s", operation, elapsed * 1000, safe_statement)


@event.listens_for(engine.sync_engine, "handle_error")
def _handle_database_error(exception_context):
    exception = exception_context.original_exception
    error_type = _error_type(exception)
    db_errors_total.labels(error_type).inc()
    started_at = getattr(exception_context.execution_context, "_smartbancs_query_started_at", None)
    if started_at is not None:
        db_query_duration_seconds.labels(_operation(exception_context.statement or ""), "error").observe(
            time.perf_counter() - started_at
        )
    operation = _operation(exception_context.statement or "")
    safe_statement = " ".join((exception_context.statement or "").split()).replace('"', "'")[:300]
    logger.error(
        "db query failed operation=%s error_type=%s sql=%s detail=%s",
        operation,
        error_type,
        safe_statement,
        str(exception).replace('"', "'")[:300],
    )


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
