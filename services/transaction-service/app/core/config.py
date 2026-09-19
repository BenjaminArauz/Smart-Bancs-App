"""
Configuración centralizada.

Todo lo que cambia entre entornos (dev/staging/prod) vive acá y se
inyecta por variables de entorno — nunca hardcodeado. Esto es clave
para desplegar en Cloud Run: los secretos (DATABASE_URL con
credenciales) se inyectan desde Secret Manager sin tocar el código
ni la imagen del contenedor.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "SmartBancs Transaction Service"
    environment: str = "development"

    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/smartbancs"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 5

    max_optimistic_retries: int = 3
    log_level: str = "INFO"

    # Servicio de IA (punto 3.3): consumido de forma asíncrona por el
    # worker de outbox, nunca en el camino síncrono de una transacción.
    ai_service_url: str = "http://localhost:8001"
    ai_request_timeout_seconds: float = 2.0
    ai_worker_poll_interval_seconds: float = 2.0
    ai_worker_batch_size: int = 20


settings = Settings()
