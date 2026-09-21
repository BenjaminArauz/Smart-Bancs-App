"""
Middleware de trazabilidad y métricas HTTP.

Toma X-Trace-Id del caller si viene (permite correlacionar con el
API Gateway) o genera uno nuevo. Lo guarda en el ContextVar para que
CUALQUIER log de esta request lo incluya automáticamente, y lo
devuelve en la respuesta para que el cliente pueda usarlo en un
reporte de incidente.

De paso, este es el único punto por el que pasa toda petición HTTP
(éxito o error, incluso 500 sin manejar), por lo que es el lugar
correcto para registrar las métricas de tráfico/latencia/errores de
punto 3.4 sin tener que instrumentar cada endpoint a mano.
"""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import trace_id_var, get_logger
from app.core.metrics import http_requests_in_flight, http_requests_total, http_request_duration_seconds

logger = get_logger("http")


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        token = trace_id_var.set(trace_id)
        start = time.perf_counter()
        # route.path (p. ej. "/api/transactions") en vez de request.url.path:
        # evita que un path con parámetros dinámicos explote la cardinalidad
        # de la métrica en Prometheus (un label por cada id distinto).
        path_template = request.url.path

        # Concurrencia real de esta instancia: la señal que Cloud Run usa
        # para decidir cuándo crear una instancia nueva (ver 3.4.3).
        http_requests_in_flight.inc()
        try:
            response = await call_next(request)
        finally:
            http_requests_in_flight.dec()
            trace_id_var.reset(token)
            route = request.scope.get("route")
            if route is not None:
                path_template = route.path

        elapsed_seconds = time.perf_counter() - start
        logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({elapsed_seconds * 1000:.1f}ms)")

        http_requests_total.labels(request.method, path_template, response.status_code).inc()
        http_request_duration_seconds.labels(request.method, path_template).observe(elapsed_seconds)

        response.headers["X-Trace-Id"] = trace_id
        return response