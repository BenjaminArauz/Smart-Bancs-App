"""
Cliente HTTP hacia el servicio de IA.

Encapsula httpx.AsyncClient con timeouts cortos y manejo de errores
"a prueba de fallos": si el servicio de IA está caído, lento, o
devuelve un error, este cliente NUNCA levanta una excepción hacia quien
lo llama (el worker de outbox) — devuelve None y deja el evento sin
marcar como procesado para reintentarlo en el próximo ciclo. La
disponibilidad de la IA nunca debe convertirse en un problema para el
resto de la plataforma.
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


class AIRiskClient:
    def __init__(self, base_url: str, timeout_seconds: float = 2.0):
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def score_transaction(self, features: dict) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(f"{self._base_url}/score", json=features)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            logger.warning(f"ai-service no disponible o respondió con error: {exc}")
            return None
