"""
Modelos ORM.

Viven en infrastructure/, separados de app/domain/entities.py a
propósito: son el detalle de CÓMO se persisten los datos (columnas,
tipos de Postgres, constraints), no QUÉ es una cuenta o una
transacción para el negocio. Los repositorios traducen entre estas
filas de BD y las entidades de dominio.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Numeric, Integer, DateTime, ForeignKey, Enum, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base
from app.domain.entities import TransactionStatus


class AccountORM(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    balance: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TransactionORM(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_transactions_idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("accounts.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(
            TransactionStatus,
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class OutboxEventORM(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RiskScoreORM(Base):
    """Resultado del scoring de IA para una transacción (punto 3.3)."""

    __tablename__ = "risk_scores"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("transactions.id"), nullable=False, unique=True)
    risk_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))