#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
echo "Deteniendo SmartBancs App..."
docker compose down

echo "Listo. (Los datos de Postgres persisten en el volumen 'postgres_data'."
echo " Para borrarlos también: docker compose down -v)"