#!/usr/bin/env bash
set -euo pipefail

echo "Probando POST /api/transactions contra la cuenta semilla..."
curl -s -X POST http://localhost:8000/api/transactions \
  -H "Content-Type: application/json" \
  -d '{
    "idempotency_key": "smoke-test-'"$(date +%s)"'",
    "account_id": "11111111-1111-1111-1111-111111111111",
    "amount": 150.00
  }' | python3 -m json.tool