# SmartBancs — Transaction Service

Microservicio REST que recibe transacciones financieras (punto 3.1
del reto). Implementa **idempotencia**, **locking optimista** para
concurrencia y el patrón **Outbox** para desacoplar la sincronización
con Bancs y el disparo de la IA del flujo transaccional principal
(ver justificación completa en `docs/documento-tecnico.md`, sección
"Desarrollo del servicio").

## Arquitectura (hexagonal / puertos y adaptadores)

```
app/
  domain/           entidades y excepciones de negocio puras (sin FastAPI ni SQLAlchemy)
  application/       casos de uso + puertos (interfaces) + DTOs internos
  infrastructure/     adaptadores concretos: engine de Postgres, modelos ORM, repositorios
  api/                capa HTTP (FastAPI): routers, inyección de dependencias, middleware, errores
    v1/                 versión actual del contrato HTTP (schemas, router, DI)
tests/                tests del caso de uso con fakes en memoria (no requieren Postgres)
```

El dominio y los casos de uso no dependen de infraestructura — se
pueden testear sin base de datos. La única capa que conoce
SQLAlchemy/Postgres es `infrastructure/`.

## Prerrequisitos

- Docker + Docker Compose (forma recomendada), **o**
- Python 3.12 y una instancia de PostgreSQL 16 accesible, si corres el servicio suelto.

## Cómo correrlo

### Opción recomendada: junto con el resto del proyecto (docker-compose)

Este servicio es parte del monorepo `smartbancs-app/` y se orquesta
desde la **raíz del repositorio**, no desde esta carpeta:

```bash
cd ../..                 # volver a la raíz de smartbancs-app/
chmod +x scripts/*.sh     # solo la primera vez
./scripts/run.sh          # construye la imagen y levanta Postgres + este servicio
./scripts/smoke-test.sh   # prueba rápida con curl
./scripts/stop.sh         # detener todo
```

El `docker-compose.yml` de la raíz ya crea las tablas y una cuenta de
prueba automáticamente (`infra/db/init.sql`) y conecta este servicio
a Postgres por red interna de Docker.

### Opción alternativa: correrlo suelto (requiere Postgres propio)

```bash
cp .env.example .env          # ajusta DATABASE_URL a tu Postgres local
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

En este caso tú eres responsable de crear las tablas — corre el DDL
de `infra/db/init.sql` contra tu base de datos antes de levantar el
servicio.

## Variables de entorno

| Variable                  | Default (local)                                              | Descripción                                   |
|----------------------------|----------------------------------------------------------------|------------------------------------------------|
| `DATABASE_URL`             | `postgresql+asyncpg://user:password@localhost:5432/smartbancs` | Cadena de conexión async a Postgres            |
| `DB_POOL_SIZE`              | `20`                                                            | Conexiones persistentes del pool               |
| `DB_MAX_OVERFLOW`           | `10`                                                            | Conexiones extra bajo picos de carga           |
| `MAX_OPTIMISTIC_RETRIES`    | `3`                                                              | Reintentos ante conflicto de versión           |
| `LOG_LEVEL`                 | `INFO`                                                           | Nivel de logging                               |
| `ENVIRONMENT`               | `development`                                                    | Etiqueta informativa del entorno               |

## Endpoint principal

```
POST /api/v1/transactions
Content-Type: application/json

{
  "idempotency_key": "uuid-o-hash-unico-por-intento",
  "account_id": "11111111-1111-1111-1111-111111111111",
  "amount": 150.00
}
```

**Respuesta (201):**
```json
{
  "transaction_id": "5c94c2ef-...",
  "status": "completed",
  "new_balance": 850.00,
  "trace_id": "4bce5bd5-...",
  "was_replayed": false
}
```

| Código | Causa                                            |
|--------|---------------------------------------------------|
| 404    | La cuenta no existe                                |
| 422    | Saldo insuficiente                                 |
| 409    | Conflicto de concurrencia (se agotaron reintentos) |
| 500    | Error inesperado (revisar logs con `trace_id`)     |

Otros endpoints:
- `GET /health` — liveness/readiness para el orquestador.
- `GET /docs` — documentación interactiva (Swagger UI).

## Probar

```bash
pytest tests/ -v
```

Los tests usan repositorios fake en memoria (`tests/test_process_transaction.py`)
y corren en milisegundos, sin necesitar Postgres levantado.