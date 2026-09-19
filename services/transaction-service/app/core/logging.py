"""
Logging estructurado.

Usamos un ContextVar para el trace_id en vez de pasarlo como
parámetro por todas las capas (dominio, aplicación, infraestructura).
Así el dominio no necesita saber nada de "trazabilidad HTTP" —
sigue siendo lógica de negocio pura — y el logging obtiene el
trace_id automáticamente en cada línea, en cualquier capa.
"""

import logging
import sys
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(TraceIdFilter())
    formatter = logging.Formatter(
        fmt='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
        '"trace_id":"%(trace_id)s","msg":"%(message)s"}'
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)