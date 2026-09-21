# Smart-Bancs-App

Solución al reto técnico SmartBancs: un monorepo de microservicios que
simula la integración de un core bancario legado (Bancs) con un pipeline
de datos, un servicio transaccional propio y un servicio de IA para
scoring de riesgo/fraude. El desarrollo, justificación y análisis teórico
de cada punto del reto está en
[`docs/documento-tecnico.md`](docs/documento-tecnico.md).

## Componentes del monorepo

| Carpeta | Qué es | Detalle |
|---|---|---|
| [`etl/`](etl/README.md) | Script ETL/ELT que limpia y estandariza un extracto crudo de transacciones de Bancs (punto 3.2) | monta features (`hour_of_day`, `day_of_week`, `is_weekend`, `amount_bucket`) que luego consume el servicio de IA |
| [`services/transaction-service/`](services/transaction-service/README.md) | Microservicio FastAPI que procesa transacciones (punto 3.1) | idempotencia, locking optimista, patrón Outbox, worker asíncrono hacia IA |
| [`services/ai-service/`](services/ai-service/README.md) | Microservicio FastAPI de scoring de riesgo (punto 3.3) | `IsolationForest` (scikit-learn) entrenado en build-time, expone `POST /score` |
| [`infra/db/init.sql`](infra/db/init.sql) | DDL de Postgres (tablas + datos semilla) | se ejecuta automáticamente al levantar el contenedor de Postgres |
| [`docs/documento-tecnico.md`](docs/documento-tecnico.md) | Documento técnico del reto | secciones 3.2 a 3.5: integración de datos, IA, observabilidad, operaciones |
| [`scripts/`](scripts/run.sh) | `run.sh` / `smoke-test.sh` / `stop.sh` | atajos sobre `docker compose` para levantar, probar y detener el entorno |

## Arquitectura

```
                 ┌───────────────┐        outbox_events        ┌──────────────┐
  cliente  ───▶  │ transaction-  │ ───(async, AIRelayWorker)──▶ │  ai-service  │
  POST /tx       │   service     │ ◀──────── risk_score ──────  │ (IsolationF.)│
                 └───────┬───────┘                              └──────────────┘
                         │
                         ▼
                    PostgreSQL 16
                 (accounts, transactions,
                  outbox_events, risk_scores)
```

- **Patrón Outbox**: cada transacción escribe su evento (`ai.recommend`) en
  la misma transacción de base de datos que la operación de negocio, para no
  perder eventos ni acoplar el llamado a IA al camino síncrono de la API.
- El **AIRelayWorker** (`services/transaction-service/app/workers/ai_relay.py`)
  hace polling del outbox con `FOR UPDATE SKIP LOCKED`, llama a `ai-service`
  vía HTTP y persiste el resultado en `risk_scores`.
- Cada servicio es independiente: Dockerfile y `requirements.txt` propios;
  `docker-compose.yml` en la raíz solo los orquesta (red, variables de
  entorno, orden de arranque).

## Cómo levantar el proyecto

Requiere Docker + Docker Compose.

```bash
chmod +x scripts/*.sh     # solo la primera vez
./scripts/run.sh          # build + levanta Postgres, transaction-service y ai-service
./scripts/smoke-test.sh   # prueba rápida end-to-end con curl
./scripts/stop.sh         # detener todo
```

Esto expone:
- `transaction-service` en `http://localhost:8000` (`/docs` para Swagger UI)
- `ai-service` en `http://localhost:8001`
- Postgres en `localhost:5432` (usuario/clave/db: `smartbancs`)

`infra/db/init.sql` crea las tablas y una cuenta de prueba automáticamente
en el primer arranque del contenedor de Postgres.

## Observabilidad

Ambos microservicios exponen métricas en formato Prometheus vía `GET
/metrics` (golden signals: tráfico, errores, latencia, saturación, más
métricas de capacidad/autoescalado para Cloud Run: concurrencia en curso e
identidad de instancia) y salud vía `GET /health` (`transaction-service`
además expone `GET /health/ready` que valida la conexión a Postgres).
Detalle de métricas, justificación de diseño, escenarios de capacidad y
runbook de incidentes en las secciones 3.4 a 3.6 de
[`docs/documento-tecnico.md`](docs/documento-tecnico.md).

## Tests

Cada servicio tiene sus propios tests unitarios/de integración (con fakes
en memoria, no requieren Postgres levantado):

```bash
cd etl && pytest
cd services/transaction-service && pytest tests/ -v
cd services/ai-service && pytest
```

## Estructura del repo

Ver el árbol completo de carpetas y archivos en la vista del workspace, o
navegar directamente por servicio: [`etl/`](etl/README.md),
[`services/transaction-service/`](services/transaction-service/README.md),
[`services/ai-service/`](services/ai-service/README.md).

## Uso de Inteligencia Artificial

Este proyecto fue desarrollado con apoyo de herramientas de IA (GitHub Copilot) en distintas partes del trabajo, y a continuación se detalla en qué puntos se usó: la **Presentación** (redacción y estructuración del contenido expuesto sobre el reto) fue generada con ayuda de IA; los **Scripts** (`run.sh`, `smoke-test.sh`, `stop.sh` en `scripts/`) fueron generados y ajustados con ayuda de IA; el **AI-Service** (`services/ai-service/`, incluyendo el entrenamiento del modelo `IsolationForest` y la API de scoring) fue implementado con ayuda de IA; y el **ETL** (`etl/transform.py` y su lógica de limpieza y generación de features) fue implementado con ayuda de IA.

---
