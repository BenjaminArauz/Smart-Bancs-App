"""
Métricas de instrumentación del servicio de IA (punto 3.4).

Mismo principio que en `transaction-service`: latencia, tráfico y
errores del único endpoint de negocio (`/score`), más el estado de
carga del modelo (saturación/disponibilidad de un recurso crítico).

Tipos: Counter = acumulado (importa la tasa entre scrapes). Histogram =
distribución en "baldes" de duración, elegidos a mano por métrica.
Gauge = valor instantáneo, sube y baja libremente.
"""

from prometheus_client import Counter, Gauge, Histogram

score_requests_total = Counter(
    "score_requests_total",
    "Total de peticiones a /score, por resultado (scored o model_unavailable)",
    ["outcome"],
)

# Baldes en segundos: la inferencia del modelo tarda microsegundos-ms
# sobre CPU (ver model.py), no tiene sentido usar los baldes por
# defecto de prometheus_client (pensados para llamadas de red, 5ms-10s).
score_duration_seconds = Histogram(
    "score_duration_seconds",
    "Duración en segundos del cálculo de /score",
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1),
)

model_loaded = Gauge(
    "model_loaded",
    "1 si el modelo de riesgo está cargado en memoria, 0 si no",
)

# --- Capacidad y autoescalado (Cloud Run, ver 3.4.3 en documento-tecnico.md) ---
score_requests_in_flight = Gauge(
    "score_requests_in_flight",
    "Peticiones a /score en curso en este instante en esta instancia",
)

app_instance_info = Gauge(
    "app_instance_info",
    "Metadata de la instancia en ejecución (valor fijo 1 mientras vive el proceso)",
    ["instance_id", "revision"],
)


