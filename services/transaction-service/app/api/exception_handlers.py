"""
Traducción de excepciones de dominio a HTTP.

Centralizado acá para que el contrato de error sea SIEMPRE el mismo
({error, message, trace_id}) sin importar en qué capa se originó el
problema, y para que el dominio nunca tenga que saber qué código
HTTP le corresponde a cada error de negocio.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import trace_id_var, get_logger
from app.domain.exceptions import AccountNotFoundError, InsufficientFundsError, ConcurrencyConflictError

logger = get_logger(__name__)

_STATUS_BY_EXCEPTION = {
    AccountNotFoundError: 404,
    InsufficientFundsError: 422,
    ConcurrencyConflictError: 409,
}


def register_exception_handlers(app: FastAPI) -> None:
    for exc_type, status_code in _STATUS_BY_EXCEPTION.items():

        async def handler(request: Request, exc, _status=status_code):
            return JSONResponse(
                status_code=_status,
                content={"error": type(exc).__name__, "message": str(exc), "trace_id": trace_id_var.get()},
            )

        app.add_exception_handler(exc_type, handler)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("error no controlado")
        return JSONResponse(
            status_code=500,
            content={"error": "InternalServerError", "message": "Ocurrió un error inesperado", "trace_id": trace_id_var.get()},
        )