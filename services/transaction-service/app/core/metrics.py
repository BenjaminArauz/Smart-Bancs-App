"""
Métricas de instrumentación (punto 3.4, "Instrumentación y Telemetría").

Se eligen los "cuatro señales de oro" (golden signals: latencia,
tráfico, errores, saturación) aplicados a este servicio, más un par de
métricas de negocio/dominio que son las que realmente ayudan a
diagnosticar un incidente en producción (ver justificación en
docs/documento-tecnico.md, sección 3.4.2).

Se usa el registro global por defecto de prometheus_client: alcanza
para un solo proceso por contenedor (nuestro caso, ver Dockerfile /
docker-compose.yml) y evita tener que pasar un registry por todas las
capas.

Cómo leer cada tipo (aparece como comentario "# TYPE" en /metrics):

- Counter: SOLO sube, nunca baja (se resetea a 0 si el proceso
  reinicia). Importa la TASA de cambio entre dos scrapes, no el valor
  absoluto (p. ej. "peticiones por segundo").
- Gauge: un valor instantáneo, sube y baja libremente (p. ej. "cuántas
  conexiones están en uso AHORA"). Se lee tal cual, sin calcular tasa.
- Histogram: cuenta cuántas observaciones cayeron en cada "balde" de
  duración. `le` (*less than or equal to*) es el límite superior del
  balde; el número al lado es cuántas observaciones fueron <= ese
  límite. Los baldes se eligen a mano por métrica (parámetro
  `buckets=`) para que coincidan con la latencia esperada de cada caso
  — no tiene sentido medir en milisegundos algo que normalmente tarda
  segundos, ni al revés.
"""

from prometheus_client import Counter, Histogram, Gauge

# --- Tráfico y latencia HTTP (borde de la aplicación) ---
http_requests_total = Counter(
    "http_requests_total",
    "Total de peticiones HTTP recibidas, por método, ruta y código de respuesta",
    ["method", "path", "status_code"],
)

# Baldes en segundos: el objetivo del servicio es responder en <2s
# (ver database.py), por eso el rango va de 10ms a 5s en vez de los
# baldes por defecto de prometheus_client (pensados para 5ms-10s genérico).
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "Duración en segundos de cada petición HTTP, por método y ruta",
    ["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

# --- Errores de negocio (contrapunto a los códigos HTTP: qué tipo de
#     falla de dominio es, no solo si fue 4xx/5xx) ---
domain_errors_total = Counter(
    "domain_errors_total",
    "Total de errores de negocio devueltos al cliente, por tipo de excepción",
    ["error_type"],
)

# --- Saturación / salud de la conexión a BD ---
db_pool_checked_out = Gauge(
    "db_pool_checked_out_connections",
    "Conexiones del pool de BD en uso en este momento",
)

# --- Negocio: transacciones procesadas (creada vs. reintento idempotente) ---
transactions_processed_total = Counter(
    "transactions_processed_total",
    "Total de transacciones procesadas, por resultado (created o replayed)",
    ["outcome"],
)

# --- Relay de IA (worker de background, el mismo patrón que Worker Bancs) ---
ai_relay_events_total = Counter(
    "ai_relay_events_total",
    "Total de eventos ai.recommend procesados por el worker, por resultado (scored, retry o discarded)",
    ["outcome"],
)

# Baldes acotados al timeout del cliente HTTP a ai-service (2s, ver
# ai_request_timeout_seconds en core/config.py): más allá de eso ya cortó.
ai_relay_event_duration_seconds = Histogram(
    "ai_relay_event_duration_seconds",
    "Duración en segundos de cada llamada del worker a ai-service",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 1.5, 2),
)

outbox_pending_events = Gauge(
    "outbox_pending_events",
    "Eventos sin procesar en el outbox en este momento, por tipo de evento",
    ["event_type"],
)


