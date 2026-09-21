#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
echo "Levantando SmartBancs App (Postgres + transaction-service)..."
docker compose up --build -d

echo ""
echo "Listo. Servicios disponibles:"
echo "  - API:      http://localhost:8000/docs"
echo "  - Health:   http://localhost:8000/health"
echo "  - Metrics:  http://localhost:8000/metrics"
echo ""
echo "Servicios de la Inteligencia Artificial:"
echo "  - API:      http://localhost:8001/docs"
echo "  - Health:   http://localhost:8001/health"
echo "  - Metrics:  http://localhost:8001/metrics"
echo ""
echo "Para ver logs en vivo:   docker compose logs -f"
echo "Para detener:            ./scripts/stop.sh"