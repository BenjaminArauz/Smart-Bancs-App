"""
Servicio de IA — punto 3.3 del reto.

Microservicio independiente y desacoplado de `transaction-service`:
expone un único endpoint de inferencia (`/score`) que recibe las
features de una transacción y devuelve un score de riesgo. No conoce
nada de Postgres, outbox, ni reglas de negocio de saldo — su único
contrato es "features in, score out", lo que permite versionarlo,
escalarlo y desplegarlo de forma completamente independiente del
servicio de transacciones (distintos ciclos de release, distinto
scaling policy, incluso distinto lenguaje si hiciera falta a futuro).
"""

import os
import time
import uuid

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.model import RiskModel
from app.schemas import TransactionFeatures, RiskScoreResponse
from app.metrics import (
    app_instance_info,
    model_loaded,
    score_duration_seconds,
    score_requests_in_flight,
    score_requests_total,
)

app = FastAPI(title="SmartBancs AI Service")

try:
    risk_model = RiskModel()
except FileNotFoundError:
    risk_model = None

model_loaded.set(1 if risk_model is not None else 0)

# Uuid por proceso, no K_REVISION (compartido por todas las réplicas de una
# misma revisión): así cada cold start de Cloud Run es una serie de tiempo
# nueva, lo que permite contar instancias distintas a lo largo del tiempo.
app_instance_info.labels(str(uuid.uuid4())[:8], os.environ.get("K_REVISION", "local")).set(1)


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "model_loaded": risk_model is not None}


@app.get("/metrics", tags=["ops"])
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/score", response_model=RiskScoreResponse, tags=["scoring"])
async def score_transaction(body: TransactionFeatures):
    if risk_model is None:
        score_requests_total.labels("model_unavailable").inc()
        raise HTTPException(status_code=503, detail="Modelo no disponible: ejecutar train_model.py")

    # Concurrencia real de esta instancia (ver 3.4.3): la señal que Cloud
    # Run usa para decidir cuándo crear una instancia nueva.
    score_requests_in_flight.inc()
    try:
        start = time.perf_counter()
        risk_score, risk_level = risk_model.score(
            {
                "amount": body.amount,
                "hour_of_day": body.hour_of_day,
                "day_of_week": body.day_of_week,
                "is_weekend": int(body.is_weekend),
            }
        )
        score_duration_seconds.observe(time.perf_counter() - start)
        score_requests_total.labels("scored").inc()
    finally:
        score_requests_in_flight.dec()
    return RiskScoreResponse(
        transaction_id=body.transaction_id,
        risk_score=risk_score,
        risk_level=risk_level,
        model_version=risk_model.model_version,
    )
