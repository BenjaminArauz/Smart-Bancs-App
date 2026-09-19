"""
Engine y sesión de base de datos.

El pool está dimensionado para alta concurrencia: pool_timeout bajo
para fallar rápido (y que el circuit breaker / retry del cliente
actúe) en vez de encolar peticiones indefinidamente cuando la BD
está saturada. pool_pre_ping evita usar conexiones muertas.
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


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


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
