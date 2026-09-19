"""
Middleware de trazabilidad.

Toma X-Trace-Id del caller si viene (permite correlacionar con el
API Gateway) o genera uno nuevo. Lo guarda en el ContextVar para que
CUALQUIER log de esta request lo incluya automáticamente, y lo
devuelve en la respuesta para que el cliente pueda usarlo en un
reporte de incidente.
"""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import trace_id_var, get_logger

logger = get_logger("http")


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        token = trace_id_var.set(trace_id)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({elapsed_ms:.1f}ms)")
        response.headers["X-Trace-Id"] = trace_id
        return response