"""
Punto de entrada de la aplicación.

Usamos el patrón "application factory" (create_app) en vez de un
`app = FastAPI()` a nivel de módulo: facilita crear instancias
frescas de la app en los tests de integración, con distinta
configuración por test si hace falta.
"""

from fastapi import FastAPI

from app.core.config import settings
from app.core.logging import configure_logging
from app.api.middleware import TraceIdMiddleware
from app.api.exception_handlers import register_exception_handlers
from app.api.router import router as transactions_router


def create_app() -> FastAPI:
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name)
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(transactions_router)

    @app.get("/health", tags=["ops"])
    async def health():
        # Endpoint de liveness/readiness para el orquestador (Cloud Run / Kubernetes).
        return {"status": "ok"}

    return app


app = create_app()