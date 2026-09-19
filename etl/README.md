# etl/ — Transformación de datos (punto 3.2 del reto)

Este script cubre la parte **práctica** del punto 3.2: simular un proceso
ETL/ELT que toma un lote de transacciones crudas "tal como saldrían" del
core legado Bancs y las deja listas para análisis o para el servicio de
recomendaciones de IA.

La parte **teórica** del punto 3.2 (estrategia de sincronización de saldos
con Bancs sin saturarlo) se documenta en
[`docs/documento-tecnico.md`](../docs/documento-tecnico.md#32-bancs-manejo-utilización-e-integración-de-datos).

## Qué problema simula

Un extracto de Bancs no llega limpio: montos con símbolo de moneda y
separadores de miles, estados con distintos códigos legados, fechas en
formatos distintos según el canal que originó la transacción, filas
duplicadas y campos vacíos. Ver [`raw_transactions_sample.csv`](raw_transactions_sample.csv).

## Qué hace `transform.py`

1. **Extract**: lee el CSV crudo.
2. **Transform** (limpieza + estandarización):
   - Normaliza montos (quita `$`, comas, espacios) a `Decimal`.
   - Mapea códigos de estado legados (`OK`, `C`, `COMPLETADA`, `ERROR`...) a
     un vocabulario único (`completed`, `failed`, `pending`, `reversed`).
   - Parsea múltiples formatos de fecha/hora y los normaliza a ISO 8601 UTC.
   - Descarta (y contabiliza el motivo) filas con datos irrecuperables:
     sin id, sin cuenta, monto inválido, estado desconocido o fecha inválida.
   - Elimina duplicados por `transaction_id`.
   - Enriquece cada fila con features derivadas útiles para análisis/IA:
     `hour_of_day`, `day_of_week`, `is_weekend`, `amount_bucket`.
3. **Load**: escribe el resultado en `output/`:
   - `transactions_clean.jsonl` — una transacción limpia por línea (JSON
     Lines), formato fácil de leer en streaming y de anexar sin reescribir
     todo el archivo; es el que consumiría un pipeline de análisis o el
     servicio de IA.
   - `transform_report.json` — resumen de la corrida (filas leídas, filas
     limpias, filas descartadas y motivo de cada descarte).

## Cómo correrlo

```bash
cd etl
python3 transform.py
# o con archivos propios:
python3 transform.py --input mi_extracto.csv --output ./output
```

No requiere dependencias externas (solo librería estándar). Para correr los
tests:

```bash
pip install -r requirements.txt
pytest
```
