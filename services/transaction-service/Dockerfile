# Build stage: instala dependencias en un venv aislado
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Runtime stage: imagen final sin herramientas de build (menor superficie de ataque y tamaño)
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app ./app

# Usuario no-root: buena práctica de seguridad para contenedores en producción
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]