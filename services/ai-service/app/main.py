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

from fastapi import FastAPI, HTTPException

from app.model import RiskModel
from app.schemas import TransactionFeatures, RiskScoreResponse

app = FastAPI(title="SmartBancs AI Service")

try:
    risk_model = RiskModel()
except FileNotFoundError:
    risk_model = None


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "model_loaded": risk_model is not None}


@app.post("/score", response_model=RiskScoreResponse, tags=["scoring"])
async def score_transaction(body: TransactionFeatures):
    if risk_model is None:
        raise HTTPException(status_code=503, detail="Modelo no disponible: ejecutar train_model.py")

    risk_score, risk_level = risk_model.score(
        {
            "amount": body.amount,
            "hour_of_day": body.hour_of_day,
            "day_of_week": body.day_of_week,
            "is_weekend": int(body.is_weekend),
        }
    )
    return RiskScoreResponse(
        transaction_id=body.transaction_id,
        risk_score=risk_score,
        risk_level=risk_level,
        model_version=risk_model.model_version,
    )
