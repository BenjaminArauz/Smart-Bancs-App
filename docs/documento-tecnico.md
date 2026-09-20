# SmartBancs App — Documento Técnico

> Nota: este documento se completa de forma incremental a medida que se
> resuelven los puntos del reto. Por ahora contiene las secciones 3.2,
> 3.3, 3.4 y 3.5; el resto (arquitectura general, 3.1 y 3.6) queda
> pendiente.

## 3.2. Bancs: Manejo, Utilización e Integración de Datos

### 3.2.1. Estrategia de sincronización (teórico)

**Problema:** Bancs es un core transaccional legado, robusto pero rígido:
no soporta un alto volumen de consultas/escrituras directas sin degradar
su rendimiento. SmartBancs App necesita procesar picos de hasta 10,000
TPS sin convertir a Bancs en un cuello de botella.

**Principio de diseño:** Bancs deja de ser la ruta síncrona del flujo
transaccional. La aplicación nueva mantiene su propia copia local del
estado (tabla `accounts` en Postgres) como fuente de verdad para
decisiones de negocio en caliente (¿hay saldo suficiente?), y la
sincronización con Bancs ocurre de forma asíncrona y desacoplada,
usando el patrón **Transactional Outbox + Event Bus**.

**Flujo de datos:**

1. El cliente envía una transacción a `transaction-service` (API REST).
2. El servicio valida saldo y actualiza `accounts` + inserta la fila en
   `transactions` **en la misma transacción de base de datos** que la
   fila en `outbox_events` (tabla vista en [infra/db/init.sql](../infra/db/init.sql)).
   Esto garantiza atomicidad: o se registran ambas, o ninguna — nunca hay
   un evento "huérfano" que Bancs nunca reciba, ni una transacción que
   Bancs reciba sin haber sido aplicada localmente.
3. La transacción responde al usuario en <2s **sin esperar a Bancs**.
4. Un proceso independiente (`Worker Bancs`, visible en el diagrama de
   despliegue) hace polling/CDC sobre `outbox_events` donde
   `processed_at IS NULL`, publica esos eventos al Event Bus (Pub/Sub) y
   los consume para aplicar la actualización de saldo contra Bancs.
5. `Worker Bancs` controla el ritmo de escritura hacia Bancs con:
   - **Batching**: agrupa N eventos o los acumula por una ventana de
     tiempo antes de aplicarlos, en vez de una llamada por transacción.
   - **Rate limiting / backpressure**: un límite de solicitudes por
     segundo hacia Bancs, independiente del volumen de entrada.
   - **Reintentos con backoff exponencial + dead-letter** para eventos
     que Bancs rechace (timeout, saldo inconsistente, etc.), sin bloquear
     el resto de la cola.
   - **Idempotencia**: cada evento lleva su `idempotency_key` /
     `trace_id`, de forma que un reintento no duplique el efecto en Bancs.
6. Si Bancs necesita exponer cambios hacia SmartBancs App (p. ej. un
   ajuste manual de saldo hecho fuera de la app), se prefiere un
   mecanismo de **Change Data Capture o extracción incremental
   periódica** (por `updated_at`/log de auditoría de Bancs) en lugar de
   que `transaction-service` consulte a Bancs en el camino síncrono de
   cada petición.

**Por qué no es peligroso tener saldo "eventualmente consistente":**
el saldo local es la fuente de verdad para autorizar nuevas
transacciones (con control optimista de concurrencia vía `version`); la
sincronización hacia Bancs es una propagación downstream, auditable y
reintentable, no una dependencia bloqueante del flujo crítico.

### 3.2.2. Transformación de datos (práctico)

Implementado en [`etl/transform.py`](../etl/transform.py) (ver
[`etl/README.md`](../etl/README.md) para el detalle completo). Resumen:

- **Entrada**: un extracto crudo de transacciones estilo Bancs
  (`etl/raw_transactions_sample.csv`), con montos con símbolos de
  moneda, códigos de estado heterogéneos, fechas en múltiples formatos,
  duplicados y campos vacíos.
- **Limpieza**: normaliza montos y fechas, mapea estados legados a un
  vocabulario único, descarta filas irrecuperables (con motivo
  registrado) y elimina duplicados por `transaction_id`.
- **Estructuración para IA/análisis**: agrega features derivadas
  (`hour_of_day`, `day_of_week`, `is_weekend`, `amount_bucket`) y escribe
  el resultado en `etl/output/transactions_clean.jsonl` (JSON Lines),
  además de un reporte de la corrida (`transform_report.json`) para
  auditar cuántas filas se limpiaron o descartaron y por qué.

## 3.3. Inteligencia Artificial: Implementación y Despliegue

### 3.3.1. Integración en código (práctico)

**Decisión de diseño:** en vez de un mock que solo devuelve un valor
fijo, se implementó un **servicio de IA real, independiente y
desplegable** ([`services/ai-service`](../services/ai-service)): un
`IsolationForest` (scikit-learn) entrenado sobre un dataset sintético
que reproduce la misma forma de features que ya produce el ETL de la
sección 3.2 (`amount`, `hour_of_day`, `day_of_week`, `is_weekend`, ver
[`etl/transform.py`](../etl/transform.py)). El objetivo del punto no es
maximizar la precisión del modelo — es demostrar el **patrón de
integración**: un servicio de IA desacoplado, consumido sin bloquear el
flujo transaccional. El pipeline de entrenamiento
([`train_model.py`](../services/ai-service/train_model.py)) es real y
reemplazable: el día que exista un dataset histórico etiquetado, solo
cambia `build_synthetic_dataset()` por una carga de datos real, sin
tocar el resto del servicio.

**Por qué no se reutilizó el modelo de clasificación de frutas:** es un
CNN de visión por computadora (clasifica imágenes en categorías fijas),
un problema completamente distinto a "puntuar el riesgo de una
transacción financiera" (features tabulares, sin etiquetas de fraude
disponibles el día 1). Se optó por un modelo no supervisado
(`IsolationForest`) apropiado para ese contexto real: aprende qué es
"comportamiento normal" y señala como riesgosas las transacciones que
se alejan de ese patrón, sin necesitar transacciones ya marcadas como
fraude.

**Arquitectura de la integración (asíncrona, no bloqueante):**

```
POST /api/transactions (transaction-service)
        │
        ▼
ProcessTransactionUseCase.execute()          ← camino síncrono, <2s
        │
        ├─ actualiza accounts (locking optimista)
        ├─ inserta transactions
        └─ inserta outbox_events:
              - "bancs.sync"     (sección 3.2)
              - "ai.recommend"   (sección 3.3)   ← todo en UNA transacción de BD
        │
        ▼
  responde al cliente YA (no espera a la IA)


AIRelayWorker (asyncio.Task en background, mismo proceso)
        │  loop independiente, poll cada N segundos
        ▼
  SELECT outbox_events WHERE event_type='ai.recommend'
         AND processed_at IS NULL FOR UPDATE SKIP LOCKED
        │
        ▼
  arma features (amount, hour_of_day, day_of_week, is_weekend)
        │
        ▼
  AIRiskClient.score_transaction() → httpx.AsyncClient
        │ POST http://ai-service:8001/score  (timeout corto, 2s)
        ▼
  ai-service (microservicio independiente, IsolationForest)
        │
        ▼
  persiste en risk_scores + marca outbox_events.processed_at
```

**Por qué esto cumple "asíncrono / no bloqueante":**

- `ProcessTransactionUseCase` ([`use_cases.py`](../services/transaction-service/app/application/use_cases.py))
  **nunca importa ni llama** al cliente de IA. Su única relación con la
  IA es escribir un evento en la misma transacción de BD (atomicidad
  garantizada, igual que con `bancs.sync`), exactamente el mismo patrón
  de **Transactional Outbox** ya usado para desacoplar de Bancs (3.2.1).
- El consumo real ocurre en [`AIRelayWorker`](../services/transaction-service/app/workers/ai_relay.py),
  un `asyncio.Task` que se arranca en el `lifespan` de
  [`main.py`](../services/transaction-service/app/main.py) y vive en un
  ciclo propio, totalmente separado del ciclo request/response de
  FastAPI. Si el servicio de IA está lento o caído, el peor caso es que
  el evento se reintenta en el siguiente poll — **la latencia de
  `POST /api/transactions` nunca se ve afectada**.
- [`AIRiskClient`](../services/transaction-service/app/infrastructure/ai_client.py)
  usa `httpx.AsyncClient` con un timeout corto (2s) y atrapa cualquier
  error HTTP: nunca propaga la excepción hacia el worker, así una caída
  del servicio de IA no puede tumbar `transaction-service`.
- `ai-service` es un **microservicio HTTP independiente**
  ([`services/ai-service`](../services/ai-service)), con su propio
  Dockerfile, requirements y ciclo de release — se puede escalar,
  versionar o reemplazar sin tocar `transaction-service` (ver
  `docker-compose.yml`, puerto `8001`).

### 3.3.2. Manejo del modelo en producción (teórico)

**Alimentación con nuevos datos:**

- El ETL (3.2.2) ya deja el dataset limpio y con las features de IA en
  `etl/output/transactions_clean.jsonl`. En producción, ese mismo
  pipeline correría de forma incremental (por lote diario o por
  streaming desde `outbox_events`/CDC) hacia un **feature store** o,
  a mínimo, una tabla de features versionada — así el mismo conjunto de
  features usado en entrenamiento es el que se sirve en inferencia
  (evita el problema de *training/serving skew*).
- Las etiquetas de fraude reales (confirmadas por el equipo de
  operaciones/fraude, o por contracargos/reclamos del cliente) se
  incorporan con delay natural (días/semanas). El pipeline de
  reentrenamiento debe tolerar ese lag: entrena con una ventana de datos
  ya "madura" (p. ej. transacciones de hace 30+ días, con su desenlace
  ya conocido), no con los datos del día actual.
- **Reentrenamiento programado** (batch, p. ej. semanal/mensual) más
  **reentrenamiento por evento** si el monitoreo de *data drift* dispara
  una alerta (ver abajo). Cada corrida de entrenamiento genera un nuevo
  artefacto versionado (`model_version`, ya presente en el esquema de
  `risk_scores` y en la respuesta de `/score`) y pasa por validación
  offline (métricas sobre un set de validación separado) antes de
  promoverse a producción — nunca se reemplaza el modelo en caliente sin
  comparar contra el modelo vigente (*shadow deployment* o *canary*: el
  modelo nuevo scorea en paralelo sin todavía decidir nada, se compara
  contra el modelo actual, y solo se promueve si mejora o iguala).

**Monitoreo de *data drift*:**

- **Drift de features (covariate shift):** comparar periódicamente la
  distribución de las features de entrada en producción (`amount`,
  `hour_of_day`, `day_of_week`, `is_weekend`, y a futuro `channel`,
  `amount_bucket`) contra la distribución usada en entrenamiento, con
  pruebas estadísticas estándar (p. ej. *Population Stability Index* o
  test de Kolmogorov–Smirnov por feature). Un PSI alto en `amount`
  indicaría, por ejemplo, que cambió el perfil de gasto de los usuarios
  (inflación, nuevo producto, temporada alta) y que el modelo puede
  estar evaluando contra un "comportamiento normal" ya desactualizado.
- **Drift de score (prediction drift):** vigilar la distribución de
  `risk_score`/`risk_level` que produce el modelo en producción a lo
  largo del tiempo (tabla `risk_scores` ya guarda cada score con
  timestamp). Un salto repentino en el % de transacciones marcadas
  `high` sin una causa de negocio conocida es una señal de alerta
  temprana, incluso antes de tener las etiquetas reales de fraude.
- **Drift de desempeño (concept drift):** una vez que llegan las
  etiquetas reales (fraude confirmado / no fraude), recalcular métricas
  de desempeño (precisión, recall, tasa de falsos positivos) sobre
  ventanas móviles y compararlas contra el baseline de la última
  validación offline. Una caída sostenida dispara el reentrenamiento
  fuera de calendario.
- Estas métricas se expondrían como series de tiempo en el stack de
  observabilidad de la plataforma (junto a los logs estructurados con
  `trace_id` que ya usa `transaction-service`), con alertas automáticas
  ante umbrales configurados — el mismo principio de "fallar rápido y
  visible" que ya se aplica al resto del sistema (p. ej.
  `pool_timeout_seconds` bajo en la conexión a BD).

**Gestión del consumo de recursos:**

- **Aislamiento de recursos:** `ai-service` corre en su propio
  contenedor/proceso (ver `docker-compose.yml`), con su propio límite de
  CPU/memoria — un pico de carga de inferencia nunca compite por
  recursos con `transaction-service`, que es el camino crítico de
  negocio.
- **Backpressure natural vía outbox:** como `AIRelayWorker` procesa en
  lotes acotados (`ai_worker_batch_size`) y a un ritmo controlado
  (`ai_worker_poll_interval_seconds`, ver
  [`core/config.py`](../services/transaction-service/app/core/config.py)),
  un pico de transacciones no se traduce en un pico de llamadas
  simultáneas a `ai-service`: la cola de eventos `ai.recommend`
  simplemente crece en `outbox_events` y se drena a un ritmo sostenible,
  igual que ya se diseñó para el `Worker Bancs` (3.2.1).
- **Costo de inferencia:** el modelo elegido (`IsolationForest`) infiere
  en microsegundos por transacción sobre CPU — no requiere GPU ni un
  runtime especializado, lo que simplifica el despliegue y mantiene el
  costo de cómputo bajo incluso a volúmenes altos. Si a futuro se
  necesitara un modelo más pesado (deep learning), el mismo patrón de
  desacople (servicio de IA independiente + outbox) permite escalar solo
  ese componente (más réplicas, GPU dedicada) sin tocar
  `transaction-service`.
- **Observabilidad de recursos:** métricas estándar de contenedor
  (CPU, memoria, latencia p95/p99 de `/score`, tasa de errores) por
  servicio, para dimensionar réplicas de `ai-service` de forma
  independiente al dimensionamiento de `transaction-service`.

## 3.4. Instrumentación y Telemetría

### 3.4.1. Mecanismos de observabilidad implementados (práctico)

Se instrumentaron los tres pilares clásicos de observabilidad —logs,
métricas y trazas de correlación— en **ambos** servicios
(`transaction-service` y `ai-service`), reusando la infraestructura de
logging ya existente (sección previa) en vez de agregar un stack nuevo:

**1. Logs estructurados con correlación (ya existían, sección 3.3, se
mantienen como pilar base):**
[`core/logging.py`](../services/transaction-service/app/core/logging.py)
emite cada línea como JSON (`ts`, `level`, `logger`, `trace_id`, `msg`) —
parseable directamente por cualquier stack de logs (Cloud Logging, ELK,
Loki) sin parsers frágiles basados en regex. El `trace_id` viaja en un
`ContextVar` ([`TraceIdMiddleware`](../services/transaction-service/app/api/middleware.py))
desde el header `X-Trace-Id` (o se genera si no viene) y aparece en
**todos** los logs de esa petición, en cualquier capa — clave para
reconstruir el recorrido completo de una transacción específica ante un
reclamo puntual ("¿qué pasó con la transacción X?").

**2. Métricas en formato Prometheus, expuestas en `GET /metrics`:**
se agregó [`prometheus-client`](../services/transaction-service/requirements.txt)
a ambos servicios y un módulo `core/metrics.py` (`app/metrics.py` en
`ai-service`) con las métricas definidas en 3.4.2. `/metrics` es un
endpoint HTTP estándar que cualquier scraper (Prometheus, Grafana
Agent, Datadog Agent) puede consumir sin configuración adicional —no se
inventó un formato propio.

- **Middleware HTTP** ([`middleware.py`](../services/transaction-service/app/api/middleware.py)):
  registra `http_requests_total` (por método, ruta y código de estado)
  y `http_request_duration_seconds` (histograma) para **toda** petición,
  exitosa o no, sin tener que instrumentar cada endpoint a mano. Se usa
  `route.path` (la plantilla, p. ej. `/api/transactions`) y no
  `request.url.path` crudo, para no explotar la cardinalidad de la
  métrica con un label distinto por cada UUID de recurso.
- **Errores de dominio** ([`exception_handlers.py`](../services/transaction-service/app/api/exception_handlers.py)):
  `domain_errors_total`, con el tipo de excepción como label
  (`InsufficientFundsError`, `ConcurrencyConflictError`,
  `AccountNotFoundError`, `UnhandledException`). Complementa al código
  HTTP: un 422 puede ser "saldo insuficiente" (comportamiento normal del
  negocio) o un bug — separarlos evita que una tasa alta de 4xx
  legítimos dispare una alerta de incidente real.
- **Métrica de negocio** ([`router.py`](../services/transaction-service/app/api/router.py)):
  `transactions_processed_total{outcome="created"|"replayed"}`. Una tasa
  alta de `replayed` es indicio de clientes reintentando de más
  (timeouts del lado del caller), no un problema del propio servicio.
- **Worker de IA** ([`ai_relay.py`](../services/transaction-service/app/workers/ai_relay.py)):
  `ai_relay_events_total{outcome="scored"|"retry"|"discarded"}`,
  `ai_relay_event_duration_seconds` (latencia de la llamada a
  `ai-service`) y `outbox_pending_events{event_type="ai.recommend"}` —un
  *gauge* con la profundidad de la cola pendiente en cada batch, la
  señal más temprana de que `ai-service` no está dando abasto, antes de
  que se traduzca en timeouts visibles.
- **`ai-service`** ([`app/main.py`](../services/ai-service/app/main.py)):
  `score_requests_total{outcome="scored"|"model_unavailable"}`,
  `score_duration_seconds` (latencia de inferencia) y el *gauge*
  `model_loaded` (0/1) — permite alertar inmediatamente si el contenedor
  levantó sin el artefacto del modelo (`risk_model.joblib`), en vez de
  descubrirlo recién cuando empiezan a fallar transacciones.

**3. Health checks diferenciados (liveness vs. readiness):**
`transaction-service` expone `GET /health` (liveness: el proceso
responde) y `GET /health/ready` (readiness: además, puede hablar con
Postgres — hace un `SELECT 1` real y devuelve 503 si falla). Esta
separación importa para el orquestador: un fallo de liveness reinicia
el contenedor; un fallo de readiness solo lo saca temporalmente del
balanceo, dándole tiempo a recuperarse (p. ej. mientras Postgres
reinicia) sin perder el estado del proceso ni provocar un *restart
loop*. `ai-service` mantiene su `/health` existente, que ya reporta
`model_loaded`.

### 3.4.2. Diseño de observabilidad (teórico)

**Criterio general:** se adoptaron las **cuatro señales de oro**
(*golden signals*: latencia, tráfico, errores, saturación) como marco
para decidir qué instrumentar, en vez de registrar "todo lo que se
pueda medir". Cada métrica elegida responde a una pregunta concreta de
diagnóstico:

| Señal | Métrica | Pregunta que responde ante un incidente |
|---|---|---|
| Latencia | `http_request_duration_seconds`, `score_duration_seconds`, `ai_relay_event_duration_seconds` | ¿La app está **lenta** o está **caída**? Un p95/p99 en aumento sostenido es degradación progresiva (detectable *antes* de la caída total), no un evento binario como un 5xx. |
| Tráfico | `http_requests_total` (por ruta) | ¿El problema correlaciona con un **pico de carga** (capacidad) o aparece con tráfico normal (bug/regresión)? Distingue "hay que escalar" de "hay que revertir un deploy". |
| Errores | `domain_errors_total`, `score_requests_total{outcome="model_unavailable"}` | ¿**Qué tipo** de falla predomina? Un salto en `ConcurrencyConflictError` apunta a contención de BD; uno en `model_unavailable` apunta a un problema de despliegue de `ai-service`, no de lógica de negocio. Sin este desglose, un 4xx/5xx agregado no dice *dónde* mirar. |
| Saturación | `outbox_pending_events`, `db_pool_checked_out_connections`, `model_loaded` | ¿Algún recurso finito (cola, pool de conexiones, modelo cargado) está por agotarse **antes** de que eso se traduzca en errores visibles para el usuario? Es la señal más útil para actuar de forma *proactiva* en vez de reactiva. |

**Por qué se justifica cada dato adicional, más allá de las cuatro
señales:**

- **`trace_id` en todos los logs:** sin un identificador de correlación,
  diagnosticar un incidente puntual ("el cliente X dice que su
  transacción falló") obliga a buscar por timestamp aproximado en logs
  de múltiples servicios y procesos concurrentes — lento y propenso a
  error. Con `trace_id` propagado end-to-end (API → outbox → worker →
  `ai-service`), reconstruir el camino completo de una transacción es
  una sola búsqueda exacta.
- **`transactions_processed_total{outcome="replayed"}` y
  `ai_relay_events_total{outcome="retry"}`:** una tasa alta de reintentos
  no siempre es un fallo del propio servicio — puede ser un cliente mal
  configurado (timeout muy corto) o una dependencia lenta. Medir el
  *outcome*, no solo el volumen total, evita atribuir la causa raíz al
  componente equivocado.
- **`outbox_pending_events` como gauge (no solo un contador de
  procesados):** un contador que crece siempre "se ve bien" aunque el
  consumidor esté cayendo en desempeño; la profundidad de cola pendiente
  es la métrica que realmente indica si el sistema está drenando trabajo
  más rápido de lo que entra o si se está acumulando deuda hacia una
  saturación futura — el mismo principio se aplicaría al `Worker Bancs`
  de la sección 3.2.1 cuando se implemente.
- **`model_loaded` como gauge binario:** el caso de falla más
  disruptivo para `ai-service` no es "el modelo predice mal", es "el
  contenedor levantó sin el artefacto" (`risk_model.joblib` faltante).
  Es barato de medir y evita que ese modo de falla pase inadvertido
  hasta que ya afectó transacciones reales.

**Qué se dejó fuera deliberadamente (y por qué):** trazas distribuidas
completas (OpenTelemetry con *spans* por llamada saliente) y un backend
de series de tiempo desplegado (Prometheus + Grafana) quedan fuera del
alcance práctico de este reto — el objetivo acá es demostrar el
**patrón correcto de instrumentación** (qué medir y cómo exponerlo) de
forma que enchufar un backend real después sea un cambio de
configuración (scrape target), no de código. `/metrics` ya sigue el
formato estándar de exposición de Prometheus, que es lo que la mayoría
de esos backends consumen de forma nativa.

## 3.5. Operaciones: incidente crítico simulado

### 3.5.1. Monitoreo implementado (práctico)

**Escenario:** durante un pico de quincena aumentan la latencia, los
`timeout` de conexión a Postgres y los reportes de deadlock. La primera
acción es consultar `GET /metrics` y buscar el mismo intervalo de tiempo
en los logs del servicio. El `X-Trace-Id` de la respuesta permite seguir
una transferencia concreta desde HTTP hasta la consulta SQL.

El servicio registra cada consulta SQL ejecutada por SQLAlchemy, sin
valores de parámetros sensibles:

- `db_query_duration_seconds{operation,outcome}` muestra latencia de
  `SELECT`, `INSERT`, `UPDATE` y `DELETE`, incluyendo consultas con error.
- `db_errors_total{error_type}` separa `pool_timeout`, `deadlock`,
  `lock_timeout`, `statement_timeout` y `database_error`.
- El logger `database` escribe operación, duración, consulta parametrizada
  truncada y detalle del error, identificando la consulta sin registrar
  montos o credenciales.
- `db_pool_checked_out_connections` muestra las conexiones ocupadas. Si
  se acerca a `DB_POOL_SIZE + DB_MAX_OVERFLOW` mientras crece
  `pool_timeout`, el cuello de botella está en el pool o en consultas
  lentas que retienen conexiones.

Alertas iniciales para Prometheus (los valores deben calibrarse con la
línea base real):

```promql
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 2
rate(db_errors_total{error_type="pool_timeout"}[5m]) > 0
rate(db_errors_total{error_type="deadlock"}[5m]) > 0
```

### 3.5.2. Runbook de respuesta (teórico-práctico)

**0. Confirmar y delimitar.** El on-call conserva `trace_id`, hora UTC y
endpoint afectado; revisa p95/p99 HTTP, tasa de errores,
`db_pool_checked_out_connections` y los contadores de errores de BD. Se
verifica `/health/ready`: si falla, la instancia se saca del balanceo sin
reiniciarla automáticamente.

**1. Estabilización inmediata.**

- Pausar consumidores no críticos, como el relay de IA, para reducir
  presión sobre Postgres; el outbox conserva los eventos.
- Reducir temporalmente el tráfico por instancia y repartirlo entre
  réplicas saludables. No aumentar `DB_POOL_SIZE` a ciegas: el total debe
  caber en `max_connections` de Postgres.
- Si una instancia está atrapada o mantiene conexiones agotadas, ponerla
  en *draining* y reiniciarla de forma controlada para liberar conexiones.
- Para un bloqueo confirmado, cancelar primero la consulta y después la
  sesión bloqueadora, con aprobación del responsable de BD:

```sql
SELECT pid, usename, state, wait_event_type, wait_event,
       now() - query_start AS age, left(query, 300) AS query
FROM pg_stat_activity
WHERE datname = current_database()
ORDER BY query_start NULLS LAST;

SELECT blocked.pid AS blocked_pid, blocking.pid AS blocking_pid,
       left(blocked.query, 200) AS blocked_query,
       left(blocking.query, 200) AS blocking_query
FROM pg_stat_activity blocked
JOIN pg_stat_activity blocking
  ON blocking.pid = ANY(pg_blocking_pids(blocked.pid));

SELECT pg_cancel_backend(<pid>);     -- primero, cancelación cooperativa
SELECT pg_terminate_backend(<pid>);  -- solo si continúa bloqueada
```

**2. Diagnóstico de causa.** `deadlock` (`SQLSTATE 40P01`) indica un
ciclo de locks; `lock_timeout` (`55P03`) indica espera por un lock;
`pool_timeout` indica que la aplicación no obtuvo conexión a tiempo; y
`statement_timeout` (`57014`) indica que Postgres canceló una consulta
demasiado larga. Se comparan los SQL del logger `database` con
`pg_stat_activity`, duración de transacciones y conexiones ocupadas antes
de cambiar parámetros.

**3. Recuperación y prevención.** Luego de estabilizar, se reactivan
consumidores gradualmente, se confirma que la cola outbox disminuye y se
comprueba que p95, errores y pool regresan a la línea base. Se conserva
el incidente con sus `trace_id` y consultas, se corrige el orden de locks
o el índice responsable y se prueba el escenario de concurrencia antes
de retirar el modo degradado.

Los cambios temporales de `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` y
`DB_POOL_TIMEOUT_SECONDS` se hacen mediante variables de entorno y un
despliegue controlado, nunca editando la imagen. La solución permanente
es corregir la consulta o la contención; aumentar timeouts solamente
oculta la saturación y puede empeorar la cola de peticiones.

