"""
Worker de IA — consumidor asíncrono del outbox (punto 3.3, "Integración en código").

Este es el mecanismo por el cual `transaction-service` "consume la IA de
forma asíncrona o no bloqueante": el endpoint `POST /api/transactions`
(ver `use_cases.py`) nunca llama al servicio de IA directamente. Solo
escribe un evento `ai.recommend` en `outbox_events`, dentro de la misma
transacción de BD que la transacción bancaria, y responde al cliente de
inmediato.

Este worker corre en un `asyncio.Task` de fondo, dentro del mismo
proceso de FastAPI (se arranca en el lifespan de `main.py`), en un loop
independiente del ciclo request/response:

  1. Hace polling sobre `outbox_events` buscando `event_type='ai.recommend'`
     con `processed_at IS NULL`, con `FOR UPDATE SKIP LOCKED` — así, si en
     el futuro se escalan varias réplicas del servicio (o un worker
     dedicado separado), no se pisan entre sí ni se bloquean.
  2. Por cada evento, arma las features de la transacción (monto, hora,
     día de semana, fin de semana) y llama a `AIRiskClient.score_transaction`.
  3. Si la llamada fue exitosa, persiste el resultado en `risk_scores` y
     marca el evento como procesado. Si falla (timeout, servicio caído),
     dej el evento sin marcar para reintentarlo en el siguiente ciclo —
     nunca lanza la excepción hacia arriba, para que un fallo del
     servicio de IA jamás tumbe el servicio de transacciones.

Es exactamente el mismo patrón que ya usa la sección 3.2 para el
"Worker Bancs" (ver docs/documento-tecnico.md) aplicado a IA en vez de a
Bancs — el outbox es un mecanismo de desacople genérico, no específico
de un solo consumidor downstream.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.infrastructure.ai_client import AIRiskClient
from app.infrastructure.database import AsyncSessionLocal
from app.infrastructure.orm_models import OutboxEventORM, TransactionORM, RiskScoreORM

logger = get_logger(__name__)

AI_EVENT_TYPE = "ai.recommend"


class AIRelayWorker:
    def __init__(self, ai_client: AIRiskClient | None = None):
        self._ai_client = ai_client or AIRiskClient(
            base_url=settings.ai_service_url,
            timeout_seconds=settings.ai_request_timeout_seconds,
        )
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_loop())
        logger.info("AIRelayWorker iniciado")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            await self._task
        logger.info("AIRelayWorker detenido")

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                processed = await self._process_batch()
            except Exception:
                logger.exception("error inesperado procesando batch de eventos ai.recommend")
                processed = 0

            if processed == 0:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=settings.ai_worker_poll_interval_seconds)
                except asyncio.TimeoutError:
                    pass

    async def _process_batch(self) -> int:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(OutboxEventORM)
                .where(OutboxEventORM.event_type == AI_EVENT_TYPE, OutboxEventORM.processed_at.is_(None))
                .order_by(OutboxEventORM.created_at)
                .limit(settings.ai_worker_batch_size)
                .with_for_update(skip_locked=True)
            )
            events = list(result.scalars())
            if not events:
                return 0

            for event in events:
                await self._process_event(session, event)

            await session.commit()
            return len(events)

    async def _process_event(self, session, event: OutboxEventORM) -> None:
        transaction_id = uuid.UUID(event.payload["transaction_id"])
        transaction = await session.get(TransactionORM, transaction_id)
        if transaction is None:
            logger.warning(f"transacción {transaction_id} no encontrada, se descarta el evento ai.recommend")
            event.processed_at = datetime.now(timezone.utc)
            return

        features = self._build_features(transaction)
        score = await self._ai_client.score_transaction(features)
        if score is None:
            # No marcamos processed_at: el evento se reintenta en el próximo ciclo.
            return

        session.add(
            RiskScoreORM(
                id=uuid.uuid4(),
                transaction_id=transaction.id,
                risk_score=score["risk_score"],
                risk_level=score["risk_level"],
                model_version=score["model_version"],
            )
        )
        event.processed_at = datetime.now(timezone.utc)
        logger.info(f"transacción {transaction.id} scoreada: {score['risk_level']} ({score['risk_score']:.2f})")

    @staticmethod
    def _build_features(transaction: TransactionORM) -> dict:
        created_at = transaction.created_at
        return {
            "transaction_id": str(transaction.id),
            "account_id": str(transaction.account_id),
            "amount": float(transaction.amount),
            "hour_of_day": created_at.hour,
            "day_of_week": created_at.weekday(),
            "is_weekend": created_at.weekday() >= 5,
        }
