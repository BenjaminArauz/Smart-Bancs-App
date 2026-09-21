# SmartBancs App — Documento Técnico

## Arquitectura General

**Escenario:** SmartBancs App debe procesar transacciones en tiempo real y ofrecer recomendaciones de IA bajo tres restricciones simultáneas:

- Alta concurrencia, con picos de hasta 10,000 TPS.
- Un core legado ("Bancs") que degrada su rendimiento si recibe consultas directas a ese volumen.
- Tiempo de respuesta inferior a 2s para las transferencias, sin que la IA introduzca bloqueo alguno.

Estas tres restricciones determinan cada decisión de arquitectura documentada en el resto de este informe.

```mermaid
flowchart LR
    C[Cliente] -->|POST /transactions| TS[transaction-service]
    TS -->|SELECT/UPDATE optimista| DB[(PostgreSQL\naccounts, transactions,\noutbox_events, risk_scores)]
    TS -->|1 transacción de BD:\nnegocio + eventos outbox| DB
    DB -.->|polling FOR UPDATE SKIP LOCKED| WB[Worker Bancs\n(async, futuro)]
    WB -->|batching + rate limit +\nreintentos/backoff| BANCS[(Core legado Bancs)]
    DB -.->|polling FOR UPDATE SKIP LOCKED| AIW[AIRelayWorker\n(implementado)]
    AIW -->|HTTP async, timeout corto| AI[ai-service\nIsolationForest]
    AI -->|risk_score| DB
    ETL[etl/transform.py] -->|features limpias\nJSON Lines| AI
    TS -.->|/metrics, /health, logs+trace_id| OBS[Observabilidad]
    AI -.->|/metrics, /health| OBS
```

**Componentes y justificación de su existencia:**

- **`transaction-service`**: implementado en Python/FastAPI, es el único componente en el camino síncrono crítico (<2s). Aísla la regla de negocio (dominio + casos de uso) de la infraestructura, lo que permite testearla sin dependencias externas y sustituir el motor de base de datos o el framework HTTP sin reescribir la lógica de negocio.
- **PostgreSQL**: seleccionado por su soporte maduro de transacciones ACID, que habilita locking optimista sin bloquear lecturas.
- **`outbox_events` (patrón Transactional Outbox)**: mecanismo que desacopla el camino síncrono de dos integraciones externas de baja confiabilidad: Bancs y el servicio de IA.
- **`Worker Bancs`**: consumidor asíncrono e independiente del outbox que aplica batching, rate limiting y reintentos con backoff hacia Bancs, de modo que el core legado nunca reciba más carga de la que puede tolerar, sin importar el volumen de entrada.
- **`AIRelayWorker` + `ai-service`**: aplican el mismo patrón de outbox, ya funcional en código. `ai-service` es un microservicio HTTP independiente, escalable o reemplazable por separado, cuya caída nunca afecta la latencia de una transacción porque el consumo es asíncrono.
- **`etl/transform.py`**: separa la limpieza y estandarización de datos crudos del resto del sistema, de modo que el modelo de IA siempre consuma datos con el mismo esquema de features.
- **Observabilidad transversal**: métricas expuestas en ambos servicios, necesarias para operar con confianza un sistema dimensionado para 10,000 TPS.

**Justificación por rendimiento, seguridad y escalabilidad:**

- **Rendimiento:** el único camino síncrono es `transaction-service` ↔ Postgres; todo lo demás es asíncrono. Esto acota la latencia del endpoint crítico al tiempo que toma una transacción local de base de datos.
- **Seguridad:** cada servicio valida su propio input, las credenciales de base de datos se inyectan por variable de entorno, y los logs registran SQL parametrizado y truncado sin exponer valores sensibles. Adicionalmente, el acceso a Postgres queda restringido a la red interna de Docker Compose, sin más exposición que el puerto necesario para desarrollo local.
- **Escalabilidad:** al ser servicios independientes, `transaction-service` y `ai-service` escalan horizontalmente por separado, según su propia demanda.

## 3.1. Infraestructura, Base de Datos y Desarrollo del Backend (Práctico)

**Suposiciones:** el reto no especifica el motor de base de datos ni el protocolo del endpoint; se asume Postgres y REST sobre HTTP.

### 3.1.1. Desarrollo del servicio (práctico)

El microservicio [`transaction-service`](../services/transaction-service) expone `POST /api/transactions`, que recibe una transacción financiera (`idempotency_key`, `account_id`, `amount`) y devuelve el nuevo saldo de la cuenta. Está implementado con **arquitectura hexagonal**, de modo que la regla de negocio no dependa de FastAPI ni de SQLAlchemy:

```
app/
  domain/           entidades y excepciones de negocio puras (Account, Transaction, OutboxEvent)
  application/       ProcessTransactionUseCase + puertos (Protocols) + DTOs
  infrastructure/     adaptadores concretos: engine de Postgres, modelos ORM, repositorios
  api/                capa HTTP: router, schemas Pydantic, middleware, manejo de errores
```

**Flujo del endpoint**:

1. Valida el payload (Pydantic, [`schemas.py`](../services/transaction-service/app/api/schemas.py)).
2. Verifica idempotencia por `idempotency_key`; si ya existe, responde con el resultado previamente calculado.
3. Debita la cuenta aplicando locking optimista con reintentos acotados.
4. Inserta `transactions` y los eventos correspondientes en `outbox_events`, en una única transacción de base de datos.
5. Responde `201` con el nuevo saldo, el `trace_id` y un indicador de si se trató de una repetición idempotente (`was_replayed`).

Códigos de respuesta:
- `404` cuenta inexistente.
- `422` saldo insuficiente.
- `409` conflicto de concurrencia tras agotar los reintentos.
- `500` error inesperado.

### 3.1.2. Base de datos: DDL/DML y control de concurrencia (práctico)

El script DDL completo se encuentra en [`infra/db/init.sql`](../infra/db/init.sql) y se ejecuta automáticamente en el primer arranque del contenedor de Postgres. Define:

- `accounts(id, balance, version)`
  - `version` es la columna clave para el control de concurrencia optimista.
  - `balance` incluye un `CHECK (balance >= 0)` como última barrera contra sobregiros, incluso ante un eventual defecto en la capa de aplicación.
- `transactions(id, idempotency_key UNIQUE, account_id, amount, status, trace_id, created_at)`
- `outbox_events(id, event_type, payload, trace_id, created_at, processed_at)`
- `risk_scores(id, transaction_id, risk_score, risk_level, model_version, created_at)`
- Datos semilla (`DML`): una cuenta de prueba con saldo inicial, para permitir probar el endpoint sin pasos previos.

**Cómo se evitan condiciones de carrera:** el caso crítico ocurre cuando dos solicitudes concurrentes debitan la misma cuenta. Se utiliza **locking optimista basado en versión**, en lugar de locks pesimistas (`SELECT ... FOR UPDATE`), para no bloquear lecturas mientras dura la transacción:

```sql
UPDATE accounts
SET balance = :nuevo_balance, version = :version + 1
WHERE id = :account_id AND version = :version_leida
```

Implementado en [`SqlAccountRepository.update_with_version_check`](../services/transaction-service/app/infrastructure/repositories.py): el `UPDATE` solo tiene efecto si `version` no cambió desde que se leyó la cuenta. Si otra solicitud ya la modificó primero, `rowcount == 0` y el caso de uso ([`ProcessTransactionUseCase.execute`](../services/transaction-service/app/application/use_cases.py)) detecta el conflicto, ejecuta `rollback()` y reintenta hasta `MAX_OPTIMISTIC_RETRIES` veces (valor por defecto `3`); si se agotan los intentos, responde `409 Conflicto de concurrencia` en lugar de esperar indefinidamente o corromper el saldo.

### 3.1.3. Automatización: infraestructura como código (práctico)

El entorno completo se levanta con un solo comando, sin pasos manuales:

```bash
./scripts/run.sh
```

- [`services/transaction-service/Dockerfile`](../services/transaction-service/Dockerfile): construye la imagen del microservicio.
- [`docker-compose.yml`](../docker-compose.yml): orquesta la red, las variables de entorno y el orden de arranque de todos los componentes.
- [`scripts/smoke-test.sh`](../scripts/smoke-test.sh): ejecuta una prueba end-to-end con `curl` sobre una transacción real.
- [`scripts/stop.sh`](../scripts/stop.sh): detiene el entorno completo.

## 3.2. Bancs: Manejo, Utilización e Integración de Datos

### 3.2.1. Estrategia de sincronización (teórico)

**Problema:** Bancs es un core transaccional legado, robusto pero rígido, que no soporta un alto volumen de consultas o escrituras directas sin degradar su rendimiento. SmartBancs App necesita procesar picos de hasta 10,000 TPS sin convertir a Bancs en un cuello de botella.

**Principio de diseño:** Bancs deja de ser la ruta síncrona del flujo transaccional. La aplicación nueva mantiene su propia copia local del estado como fuente de verdad para las decisiones de negocio en caliente, y la sincronización con Bancs ocurre de forma asíncrona y desacoplada, mediante el patrón **Transactional Outbox + Event Bus**.

**Flujo de datos:**

1. El cliente envía una transacción a `transaction-service`.
2. El servicio valida el saldo, actualiza `accounts` e inserta la fila en `transactions` **en la misma transacción de base de datos** que la fila en `outbox_events`, lo que garantiza atomicidad: ambas se registran, o ninguna lo hace.
3. La transacción responde al usuario en menos de 2s, **sin esperar a Bancs**.
4. Un proceso independiente realiza polling/CDC sobre `outbox_events` donde `processed_at IS NULL`, publica esos eventos al Event Bus (Pub/Sub) y los consume para aplicar la actualización de saldo contra Bancs.
5. `Worker Bancs` controla el ritmo de escritura hacia Bancs mediante:
   - **Batching**: agrupa N eventos, o los acumula durante una ventana de tiempo, antes de aplicarlos, en lugar de emitir una llamada por transacción.
   - **Rate limiting / backpressure**: impone un límite de solicitudes por segundo hacia Bancs, independiente del volumen de entrada.
   - **Reintentos con backoff exponencial y dead-letter**, para los eventos que Bancs rechace (timeout, saldo inconsistente, etc.), sin bloquear el resto de la cola.
   - **Idempotencia**: cada evento incluye su `idempotency_key` / `trace_id`, de modo que un reintento no duplique el efecto sobre Bancs.

**Por qué no representa un riesgo tener un saldo "eventualmente consistente":** el saldo local es la fuente de verdad para autorizar nuevas transacciones.

### 3.2.2. Transformación de datos (práctico)

Implementado en [`etl/transform.py`](../etl/transform.py). Resumen:

- **Entrada**: un extracto crudo de transacciones estilo Bancs (`etl/raw_transactions_sample.csv`), con montos con símbolos de moneda, códigos de estado heterogéneos, fechas en múltiples formatos, duplicados y campos vacíos.
- **Limpieza**: normaliza montos y fechas, mapea estados legados a un vocabulario único, descarta filas irrecuperables (con el motivo registrado) y elimina duplicados por `transaction_id`.
- **Estructuración para IA/análisis**: agrega características derivadas, como `hour_of_day`, `day_of_week`, `is_weekend` y `amount_bucket`, y escribe el resultado en `etl/output/transactions_clean.jsonl` (JSON Lines), además de un reporte de la corrida que audita cuántas filas se limpiaron o descartaron, y por qué motivo.

## 3.3. Inteligencia Artificial: Implementación y Despliegue

### 3.3.1. Integración en código (práctico)

**Decisión de diseño:** se implementó un **servicio de IA real, independiente y desplegable** ([`services/ai-service`](../services/ai-service)), basado en un `IsolationForest` entrenado sobre un dataset sintético que reproduce la misma forma de features que ya produce el ETL. El objetivo de este punto no es maximizar la precisión del modelo, sino demostrar el **patrón de integración**: un servicio de IA desacoplado, consumido sin bloquear el flujo transaccional. El pipeline de entrenamiento ([`train_model.py`](../services/ai-service/train_model.py)) es real y reemplazable: el día que exista un dataset histórico etiquetado, basta con reemplazar `build_synthetic_dataset()` por una carga de datos real, sin modificar el resto del servicio.

**Arquitectura de la integración (asíncrona, no bloqueante):**

```
POST /api/transactions (transaction-service)
        │
        ▼
ProcessTransactionUseCase.execute()
        │
        ├─ actualiza accounts (locking optimista)
        ├─ inserta transactions
        └─ inserta outbox_events:
              - "bancs.sync"     (sección 3.2)
              - "ai.recommend"   (sección 3.3)
        │
        ▼
  responde al cliente de inmediato


AIRelayWorker
        │  
        ▼
  SELECT outbox_events WHERE event_type='ai.recommend'
         AND processed_at IS NULL FOR UPDATE SKIP LOCKED
        │
        ▼
  arma features (amount, hour_of_day, day_of_week, is_weekend)
        │
        ▼
  AIRiskClient.score_transaction() → httpx.AsyncClient
        │ POST http://ai-service:8001/score 
        ▼
  ai-service (microservicio independiente, IsolationForest)
        │
        ▼
  persiste en risk_scores + marca outbox_events.processed_at
```

### 3.3.2. Manejo del modelo en producción (teórico)

**Alimentación con nuevos datos:**

- El ETL ya deja el dataset limpio, con las features de IA, en `etl/output/transactions_clean.jsonl`. En producción, ese mismo pipeline se ejecutaría de forma incremental hacia una tabla de features versionada.
- **Reentrenamiento programado**, disparado además si el monitoreo de *data drift* activa una alerta. Cada corrida de entrenamiento genera un artefacto nuevo y versionado (`model_version`, ya presente en el esquema de `risk_scores` y en la respuesta de `/score`), que pasa por validación antes de promoverse a producción; nunca se reemplaza el modelo vigente sin una comparación previa.

**Monitoreo de *data drift*:**

- **Drift de features:** compara periódicamente la distribución de las features de entrada en producción (`amount`, `hour_of_day`, `day_of_week`, `is_weekend`, y a futuro `channel`, `amount_bucket`) contra la distribución utilizada en entrenamiento. Un PSI elevado en `amount` indicaría, por ejemplo, un cambio en el perfil de gasto de los usuarios (inflación, nuevo producto, temporada alta), lo que implicaría que el modelo evalúa contra un "comportamiento normal" desactualizado.
- **Drift de score:** monitorea la distribución de `risk_score`/`risk_level` que produce el modelo en producción a lo largo del tiempo. Un incremento repentino en el porcentaje de transacciones marcadas `high`, sin una causa de negocio conocida, constituye una señal de alerta temprana, incluso antes de disponer de las etiquetas reales de fraude.
- **Drift de desempeño:** una vez que se dispone de las etiquetas reales (fraude confirmado o descartado), recalcula métricas de desempeño sobre ventanas móviles y las compara contra el baseline de la última validación offline.

**Gestión del consumo de recursos:**

- **Aislamiento de recursos:** `ai-service` corre en su propio contenedor/proceso (ver `docker-compose.yml`), con su propio límite de CPU/memoria, de modo que un pico de carga de inferencia nunca compite por recursos con `transaction-service`.
- **Costo de inferencia:** el modelo seleccionado infiere en microsegundos por transacción sobre CPU, sin requerir GPU ni un runtime especializado, lo que simplifica el despliegue y mantiene bajo el costo de cómputo incluso a volúmenes altos. Si a futuro se requiriera un modelo más pesado (deep learning), el mismo patrón de desacople permitiría escalar únicamente ese componente, sin modificar `transaction-service`.
- **Observabilidad de recursos:** métricas estándar de contenedor por servicio, que permiten dimensionar las réplicas de `ai-service` de forma independiente.

## 3.4. Instrumentación y Telemetría

### 3.4.1. Mecanismos de observabilidad implementados (práctico)

Se instrumentaron los tres pilares clásicos de observabilidad (logs, métricas y trazas de correlación) en **ambos** servicios (`transaction-service` y `ai-service`), reutilizando la infraestructura de logging ya existente en lugar de introducir un stack adicional:

**1. Logs estructurados con correlación:** [`core/logging.py`](../services/transaction-service/app/core/logging.py) emite cada línea en formato JSON, consumible directamente por cualquier stack de logs, sin recurrir a parsers frágiles basados en expresiones regulares. El `trace_id` aparece en **todos** los logs de una petición, en cualquier capa, lo que permite reconstruir el recorrido completo de una transacción específica ante un reclamo puntual ("¿qué ocurrió con la transacción X?").

**2. Métricas en formato Prometheus, expuestas en `GET /metrics`:** se incorporó [`prometheus-client`](../services/transaction-service/requirements.txt) a ambos servicios; `/metrics` es un endpoint HTTP estándar que cualquier scraper puede consumir sin configuración adicional.

- **Middleware HTTP** ([`middleware.py`](../services/transaction-service/app/api/middleware.py)): registra `http_requests_total`, `http_request_duration_seconds` (histograma) y `http_requests_in_flight` (concurrencia real) para **toda** petición, exitosa o no, sin necesidad de instrumentar cada endpoint de forma individual.
- **Errores de dominio** ([`exception_handlers.py`](../services/transaction-service/app/api/exception_handlers.py)): `domain_errors_total`, con el tipo de excepción como etiqueta.
- **Base de datos** ([`database.py`](../services/transaction-service/app/infrastructure/database.py)): `db_query_duration_seconds{operation,outcome}` (latencia por tipo de consulta) y `db_errors_total{error_type}` (`deadlock`, `lock_timeout`, `pool_timeout`, `statement_timeout`, `database_error`), capturados mediante hooks de SQLAlchemy, sin instrumentar cada repositorio.
- **Métrica de negocio** ([`router.py`](../services/transaction-service/app/api/router.py)): `transactions_processed_total{outcome="created"|"replayed"}`. Una tasa elevada de `replayed` es indicio de clientes reintentando en exceso, no necesariamente de un problema del propio servicio.
- **Worker de IA** ([`ai_relay.py`](../services/transaction-service/app/workers/ai_relay.py)): `ai_relay_events_total{outcome="scored"|"retry"|"discarded"}`, `ai_relay_event_duration_seconds` (latencia de la llamada a `ai-service`) y `outbox_pending_events{event_type="ai.recommend"}`.
- **`ai-service`** ([`app/main.py`](../services/ai-service/app/main.py)): `score_requests_total{outcome="scored"|"model_unavailable"}`, `score_duration_seconds` (latencia de inferencia) y `score_requests_in_flight` (concurrencia real).
- **Capacidad y autoescalado** (ambos servicios, ver 3.4.3): `app_instance_info{instance_id,revision}`, que permite contar instancias distintas a lo largo del tiempo.

**3. Health checks diferenciados (liveness vs. readiness):** `transaction-service` expone `GET /health` y `GET /health/ready` (readiness: verifica la conexión a Postgres mediante un `SELECT 1` real y devuelve `503` si falla).

### 3.4.2. Diseño de observabilidad (teórico)

**Criterio general:** se adoptaron las señales de latencia, tráfico, errores y saturación como criterio para decidir qué instrumentar, en lugar de registrar indiscriminadamente todo lo medible.

| Señal | Métrica | Pregunta que responde ante un incidente |
|---|---|---|
| Latencia | `http_request_duration_seconds`, `score_duration_seconds`, `ai_relay_event_duration_seconds` | ¿La aplicación está **lenta** o está **caída**? |
| Tráfico | `http_requests_total` | ¿El problema correlaciona con un **pico de carga**, o se presenta con tráfico normal? Permite distinguir entre "hay que escalar" y "hay que revertir un despliegue". |
| Errores | `domain_errors_total`, `score_requests_total{outcome="model_unavailable"}` | ¿Qué **tipo** de falla predomina? |
| Saturación | `outbox_pending_events`, `db_pool_checked_out_connections`, `model_loaded` | ¿Algún recurso finito está por agotarse **antes** de traducirse en errores visibles para el usuario? Es la señal más útil para actuar de forma proactiva, en lugar de reactiva. |

**Justificación de los datos seleccionados**

- **`trace_id` en todos los logs:** sin un identificador de correlación, diagnosticar un incidente puntual exige buscar por timestamp aproximado en logs de múltiples servicios y procesos concurrentes, un proceso lento y propenso a error. Con el `trace_id` propagado, reconstruir el camino completo de una transacción se reduce a una única búsqueda exacta.
- **`transactions_processed_total{outcome="replayed"}` y `ai_relay_events_total{outcome="retry"}`:** una tasa alta de reintentos no siempre constituye un fallo del propio servicio. Medir el *outcome*, y no solo el volumen total, evita atribuir la causa raíz al componente equivocado.
- **`outbox_pending_events`:** la profundidad de la cola pendiente indica de forma directa si el sistema está drenando trabajo más rápido de lo que ingresa, o si está acumulando deuda hacia una saturación futura.
- **`model_loaded`:** uno de los modos de falla más disruptivos ocurre cuando el contenedor de `ai-service` se levanta sin el modelo cargado.

### 3.4.3. Métricas de capacidad y autoescalado en Google Cloud (teórico-práctico)

**Contexto de despliegue asumido:** ambos servicios corren en **Cloud Run** (contenedores sin estado, expuestos por HTTP, en línea con el `Dockerfile` que ya posee cada servicio) y Postgres en **Cloud SQL**. Cloud Run escala principalmente por **concurrencia**: cada instancia acepta hasta N peticiones simultáneas (`--concurrency`, valor por defecto 80, máximo 1000 por instancia); cuando una instancia existente alcanza ese límite, Cloud Run crea una instancia nueva, hasta el tope configurado en `--max-instances`. Esta mecánica es precisamente lo que se instrumentó:

- **`http_requests_in_flight` / `score_requests_in_flight`:** equivalente local, medido dentro del proceso, de la señal de concurrencia que Cloud Run utiliza para decidir si crear una instancia adicional. Compararla contra el valor configurado en `--concurrency` indica qué tan cerca está una instancia de disparar un escalamiento.
- **`app_instance_info{instance_id,revision}`:** gauge fijo en 1 por instancia, con un identificador generado al arrancar el proceso (no `K_REVISION`, que es compartido por todas las réplicas de una misma revisión). Contar cuántas series de tiempo distintas de `instance_id` aparecen en una ventana permite responder **"cada cuánto se crea una instancia nueva"**, sin depender exclusivamente del panel nativo de Cloud Monitoring.
- **`http_request_duration_seconds` / `score_duration_seconds`:** el costo de un *cold start* (instancia nueva) se manifiesta como una latencia p99 más alta inmediatamente después de que aparece un `instance_id` nuevo, visible al cruzar ambas métricas en un mismo panel.

**Fórmula de dimensionamiento aproximada:**

```
instancias necesarias ≈ techo( TPS_objetivo × p95_latencia_segundos / concurrencia_por_instancia )
```

**Escenarios ficticios: qué soporta la solución en su estado actual y qué no:**

| Escenario | Supuesto | ¿Lo soporta? | Justificación (con las métricas ya expuestas) |
|---|---|---|---|
| Carga sostenida normal | 500 TPS, p95 de `http_request_duration_seconds` ≈ 80ms, `--concurrency=80` | **Sí**, con 1-2 instancias | `500 × 0.08 / 80 ≈ 0.5` → una instancia es suficiente; `http_requests_in_flight` se mantendría muy por debajo de 80. |
| Pico anunciado (10,000 TPS, objetivo del reto) | Mismo p95 de 80ms | **Sí, en términos de cómputo** | `10000 × 0.08 / 80 ≈ 10` instancias; Cloud Run soporta cientos de instancias por revisión, por lo que el cómputo no constituye el cuello de botella. |
| Mismo pico de 10,000 TPS, sin ajustar el pool de base de datos | `DB_POOL_SIZE=20` + `DB_MAX_OVERFLOW=10` por instancia, multiplicado por 10 instancias, equivale a 300 conexiones simultáneas a Postgres | **No, sin cambios adicionales** | Un Cloud SQL de tamaño moderado tiene un límite de conexiones muy inferior a esa cifra; `db_pool_checked_out_connections` y `db_errors_total{error_type="pool_timeout"}` subiendo de forma conjunta confirmarían el cuello de botella antes de que el usuario perciba un error 5xx. Mitigación recomendada: un *connection pooler* externo (PgBouncer o Cloud SQL Auth Proxy con pooling), en lugar de incrementar el pool de cada instancia sin criterio. |
| Ráfaga instantánea (0 a 10,000 TPS en segundos, sin rampa) | `--min-instances=0` o un valor bajo | **No, durante los primeros segundos** | Cloud Run no crea todas las instancias necesarias de forma instantánea: existe un límite de instancias nuevas por segundo, y cada una asume el costo de un *cold start*. `app_instance_info` mostraría múltiples `instance_id` nuevos apareciendo en ráfaga, y `http_request_duration_seconds` reflejaría un pico de latencia hasta que la flota complete su escalamiento. Mitigación recomendada: `--min-instances` mayor a 0, para mantener capacidad activa de base, junto con el patrón Outbox (secciones 3.2/3.3), que absorbe el excedente sin bloquear al usuario. |
| `ai-service` bajo el mismo pico | Inferencia en microsegundos por transacción, sobre CPU (ver `model.py`) | **Sí, con holgura considerable** | `score_duration_seconds` se ubica en el orden de los milisegundos; el mismo cálculo de instancias necesarias arroja una cifra considerablemente menor que la de `transaction-service`, lo que lo convierte en el componente con mayor margen antes de aproximarse a un cuello de botella. |

**Conclusión:** con la instrumentación actual, el cómputo de ambos servicios en Cloud Run escala con holgura para el pico de 10,000 TPS definido en el reto. El límite real se encuentra en Postgres (conexiones concurrentes) y en la velocidad de arranque en frío de instancias nuevas; ambos factores ya son observables con las métricas descritas en esta sección, sin requerir herramientas adicionales para su detección.

## 3.5. Operaciones: incidente crítico simulado

### 3.5.1. Monitoreo implementado (práctico)

**Escenario:** durante un pico de quincena aumentan la latencia, los `timeout` de conexión a Postgres y los reportes de deadlock. La primera acción es consultar `GET /metrics` y examinar el mismo intervalo de tiempo en los logs del servicio.

El servicio registra cada consulta SQL, sin exponer valores de parámetros sensibles:

- `db_query_duration_seconds{operation,outcome}` muestra la latencia de `SELECT`, `INSERT`, `UPDATE` y `DELETE`, incluidas las consultas que finalizaron en error.
- `db_errors_total{error_type}` distingue entre `pool_timeout`, `deadlock`, `lock_timeout`, `statement_timeout` y `database_error`.
- `db_pool_checked_out_connections` muestra las conexiones ocupadas. Si su valor se aproxima a `DB_POOL_SIZE + DB_MAX_OVERFLOW` mientras crece `pool_timeout`, el cuello de botella se ubica en el pool o en consultas lentas que retienen conexiones.

### 3.5.2. Runbook de respuesta (teórico-práctico)

Se verifica `/health/ready`: si el chequeo falla, la instancia se retira del balanceo sin reiniciarla automáticamente.

**1. Estabilización inmediata.**

- Pausar consumidores no críticos, como el relay de IA, para reducir la presión sobre Postgres; el outbox conserva los eventos pendientes.
- Reducir temporalmente el tráfico por instancia y redistribuirlo entre réplicas saludables, sin incrementar `DB_POOL_SIZE` de forma arbitraria.
- Si una instancia queda atrapada o mantiene conexiones agotadas, reiniciarla de forma controlada para liberar dichas conexiones.
- Ante un bloqueo confirmado, cancelar primero la consulta y luego la sesión bloqueadora, con aprobación del responsable de base de datos.

**2. Diagnóstico de causa.**

- `deadlock` (`SQLSTATE 40P01`) indica un ciclo de locks.
- `lock_timeout` (`55P03`) indica espera por un lock.
- `pool_timeout` indica que la aplicación no obtuvo una conexión a tiempo.
- `statement_timeout` (`57014`) indica que Postgres canceló una consulta excesivamente larga. Se comparan los registros SQL del logger `database` con `pg_stat_activity`, la duración de las transacciones y las conexiones ocupadas, antes de modificar cualquier parámetro.

**3. Recuperación y prevención.** Tras la estabilización, se reactivan los consumidores de forma gradual y se confirma que la cola del outbox disminuye, y que errores y pool retornan a su línea base.

Los cambios temporales sobre `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` y `DB_POOL_TIMEOUT_SECONDS` se realizan mediante variables de entorno y un despliegue controlado, nunca editando la imagen directamente. La solución permanente consiste en corregir la consulta o la contención subyacente; aumentar los timeouts únicamente oculta la saturación y puede agravar la cola de peticiones.

## 3.6. Operaciones: Gestión de Incidentes TI

### 3.6.1. Escalamiento

Si el incidente afecta directamente el dinero de los usuarios (transacciones perdidas o duplicadas), se escala primero al responsable de base de datos y al dueño del servicio (`transaction-service`); si el impacto es de negocio (SLA de disponibilidad, usuarios afectados a gran escala), se notifica adicionalmente a soporte/atención al cliente y a un responsable de producto, de modo que puedan comunicar el estado hacia el exterior sin esperar a la resolución del incidente. Cada escalamiento se realiza con el `trace_id` y la ventana de tiempo del incidente ya identificados, no con una descripción general; el objetivo es que la siguiente persona en la cadena pueda actuar de inmediato, sin repetir el diagnóstico desde cero.

### 3.6.2. Estructura del informe post mortem

Enfocado en el sistema y el proceso, sin atribuir responsabilidad a personas:

1. **Resumen ejecutivo:** responde a las preguntas ¿qué ocurrió?, ¿cuánto duró? y ¿a quién afectó (número de transacciones/usuarios)?
2. **Línea de tiempo:** desde la primera alerta hasta la resolución, con la hora UTC de cada acción tomada y su efecto observado, reconstruible directamente desde los logs mediante `trace_id`.
3. **Causa raíz:** el ciclo de locks o la contención específica, y el factor que lo originó, distinguiendo causa raíz de síntoma.
4. **Impacto:** transacciones fallidas frente a reintentadas exitosamente, duración total de la degradación, y si hubo pérdida de datos.
5. **Qué funcionó y qué no:** si las alertas se activaron a tiempo, si el runbook de 3.5.2 resultó suficiente o fue necesario improvisar, y si la telemetría existente fue adecuada para el diagnóstico o faltó alguna métrica o log.
6. **Acciones de seguimiento:** una lista concreta, con responsable y fecha asignados, no una simple declaración de intención.

### 3.6.3. Acciones preventivas

**En infraestructura:**

- Alertas proactivas sobre las métricas de saturación ya expuestas, orientadas a actuar antes de que se conviertan en errores, no únicamente a notificar el error ya ocurrido.
- Límites de conexión (`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`) calibrados mediante pruebas de carga que simulen el pico real (10,000 TPS), en lugar de valores por defecto arbitrarios, junto con `statement_timeout` a nivel de Postgres como última barrera contra una consulta descontrolada.

**En código:**

- Revisar y estandarizar el **orden de adquisición de locks** en toda ruta que involucre más de una fila o tabla.
- Mantener las transacciones de base de datos lo más breves posible; cualquier código nuevo que afecte a la base de datos debe seguir el mismo principio, para no ampliar la ventana de contención.
- Incorporar un índice cuando el post mortem identifique una consulta lenta que retenga locks por más tiempo del necesario.

