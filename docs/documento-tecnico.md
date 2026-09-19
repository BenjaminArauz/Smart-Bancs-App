# SmartBancs App — Documento Técnico

> Nota: este documento se completa de forma incremental a medida que se
> resuelven los puntos del reto. Por ahora contiene las secciones 3.2 y
> 3.3; el resto (arquitectura general, 3.1, 3.4–3.6) queda pendiente.

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

