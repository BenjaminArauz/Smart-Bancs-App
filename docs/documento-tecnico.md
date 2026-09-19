# SmartBancs App — Documento Técnico

> Nota: este documento se completa de forma incremental a medida que se
> resuelven los puntos del reto. Por ahora contiene la sección 3.2; el
> resto de las secciones (arquitectura general, 3.1, 3.3–3.6) quedan
> pendientes.

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
