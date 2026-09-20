"""
Punto de entrada de la aplicación.

Usamos el patrón "application factory" (create_app) en vez de un
`app = FastAPI()` a nivel de módulo: facilita crear instancias
frescas de la app en los tests de integración, con distinta
configuración por test si hace falta.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics import db_pool_checked_out
from app.api.middleware import TraceIdMiddleware
from app.api.exception_handlers import register_exception_handlers
from app.api.router import router as transactions_router
from app.infrastructure.database import AsyncSessionLocal, engine
from app.workers.ai_relay import AIRelayWorker

logger = get_logger("health")


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
        # Liveness: el proceso está vivo y puede responder HTTP.
        return {"status": "ok"}

    @app.get("/health/ready", tags=["ops"])
    async def readiness(response: Response):
        # Readiness: además de vivo, puede hacer su trabajo (hablar con la
        # BD). El orquestador usa esto para sacar la instancia del balanceo
        # sin matarla, dándole tiempo a recuperarse (p. ej. DB reiniciando).
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
            return {"status": "ok", "database": "up"}
        except Exception:
            logger.exception("readiness check falló: base de datos no disponible")
            response.status_code = 503
            return {"status": "degraded", "database": "down"}

    @app.get("/metrics", tags=["ops"])
    async def metrics():
        # Formato de exposición Prometheus: cualquier scraper estándar
        # (Prometheus, Grafana Agent, Datadog Agent) lo consume sin config
        # adicional (ver 3.4.2 en docs/documento-tecnico.md).
        # El pool no emite eventos propios: se lee el valor actual al
        # momento del scrape en vez de mantenerlo actualizado en background.
        db_pool_checked_out.set(engine.pool.checkedout())
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()