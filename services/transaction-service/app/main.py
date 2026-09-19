"""
Punto de entrada de la aplicación.

Usamos el patrón "application factory" (create_app) en vez de un
`app = FastAPI()` a nivel de módulo: facilita crear instancias
frescas de la app en los tests de integración, con distinta
configuración por test si hace falta.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.logging import configure_logging
from app.api.middleware import TraceIdMiddleware
from app.api.exception_handlers import register_exception_handlers
from app.api.router import router as transactions_router
from app.workers.ai_relay import AIRelayWorker


def create_app() -> FastAPI:
    configure_logging(settings.log_level)

    ai_relay_worker = AIRelayWorker()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # El worker de IA corre en background durante toda la vida del
        # proceso, desacoplado del ciclo request/response (ver punto 3.3).
        ai_relay_worker.start()
        yield
        await ai_relay_worker.stop()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(transactions_router)

    @app.get("/health", tags=["ops"])
    async def health():
        # Endpoint de liveness/readiness para el orquestador (Cloud Run / Kubernetes).
        return {"status": "ok"}

    return app


app = create_app()