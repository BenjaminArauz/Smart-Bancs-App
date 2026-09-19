# ai-service

Microservicio de scoring de riesgo/fraude — punto 3.3 del reto.

## Qué es

Un servicio HTTP independiente, sin estado, que recibe las features de
una transacción (monto, hora, día de la semana, fin de semana) y
devuelve un `risk_score` (0 a 1) y un `risk_level` (`low`/`medium`/`high`).

El modelo es un `IsolationForest` (scikit-learn) entrenado sobre un
dataset sintético que imita la distribución de transacciones normales
vs. atípicas (ver [`train_model.py`](train_model.py)). No es un mock:
es un modelo real, entrenado y evaluable, pero deliberadamente simple
— el objetivo de este punto del reto es demostrar el patrón de
integración (servicio de IA desacoplado + consumo no bloqueante desde
`transaction-service`), no maximizar el poder predictivo del modelo.

## Levantar el servicio localmente

```bash
cd services/ai-service
pip install -r requirements.txt
python train_model.py      # genera model/risk_model.joblib
uvicorn app.main:app --reload --port 8001
```

## Endpoints

- `GET /health` → `{"status": "ok", "model_loaded": true}`
- `POST /score`

```json
// request
{
  "transaction_id": "b3f1...",
  "account_id": "11111111-1111-1111-1111-111111111111",
  "amount": 1500.0,
  "hour_of_day": 3,
  "day_of_week": 6,
  "is_weekend": true
}

// response
{
  "transaction_id": "b3f1...",
  "risk_score": 0.82,
  "risk_level": "high",
  "model_version": "risk-model-v1"
}
```

## Cómo se consume

`transaction-service` NO llama a este servicio en el camino síncrono de
`POST /api/transactions`. Lo consume de forma asíncrona vía un worker
en background que procesa eventos `ai.recommend` de la tabla
`outbox_events` (ver [`app/workers/ai_relay.py`](../transaction-service/app/workers/ai_relay.py)
en `transaction-service` y la sección 3.3 de
[`docs/documento-tecnico.md`](../../docs/documento-tecnico.md)).

## Tests

```bash
pytest
```
