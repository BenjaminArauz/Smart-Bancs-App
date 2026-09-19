"""
ETL de transacciones — punto 3.2 del reto (transformación de datos).

Simula el proceso que tomaría un extracto crudo del core legado "Bancs"
(formatos inconsistentes, nulos, duplicados) y lo convierte en un dataset
limpio y estructurado, listo para ser consumido por análisis o por el
servicio de recomendaciones de IA.

Uso:
    python transform.py [--input raw_transactions_sample.csv] [--output output/]

Solo usa la librería estándar de Python a propósito: es un script de
soporte, no un servicio productivo, y no vale la pena cargarlo con
dependencias (p. ej. pandas) para un volumen de datos por lote pequeño.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

logger = logging.getLogger("etl.transform")

# Bancs (legado) usa sus propios códigos de estado; los normalizamos a un
# vocabulario único y estable que el resto de la plataforma pueda consumir.
STATUS_MAP = {
    "completada": "completed",
    "completed": "completed",
    "ok": "completed",
    "c": "completed",
    "pendiente": "pending",
    "pending": "pending",
    "error": "failed",
    "failed": "failed",
    "reversada": "reversed",
    "reversed": "reversed",
}

TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
)

_AMOUNT_STRIP_RE = re.compile(r"[^0-9.\-]")


@dataclass(frozen=True)
class CleanTransaction:
    transaction_id: str
    account_id: str
    amount: str  # Decimal serializado como string para no perder precisión en JSON
    status: str
    channel: str
    timestamp: str  # ISO 8601 UTC
    description: str
    hour_of_day: int
    day_of_week: int
    is_weekend: bool
    amount_bucket: str


class TransformStats:
    def __init__(self) -> None:
        self.rows_read = 0
        self.rows_clean = 0
        self.rows_dropped = 0
        self.duplicates_skipped = 0
        self.drop_reasons: dict[str, int] = {}

    def record_drop(self, reason: str) -> None:
        self.rows_dropped += 1
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + 1

    def as_dict(self) -> dict:
        return {
            "rows_read": self.rows_read,
            "rows_clean": self.rows_clean,
            "rows_dropped": self.rows_dropped,
            "duplicates_skipped": self.duplicates_skipped,
            "drop_reasons": self.drop_reasons,
        }


def parse_amount(raw: str | None) -> Decimal | None:
    """Limpia montos con símbolos de moneda, separadores de miles o vacíos."""
    if raw is None:
        return None
    stripped = _AMOUNT_STRIP_RE.sub("", raw.replace(",", ""))
    if not stripped:
        return None
    try:
        amount = Decimal(stripped)
    except InvalidOperation:
        return None
    if amount <= 0:
        return None
    return amount


def normalize_status(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip().lower()
    return STATUS_MAP.get(key)


def parse_timestamp(raw: str | None) -> datetime | None:
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    for fmt in TIMESTAMP_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def amount_bucket(amount: Decimal) -> str:
    if amount < Decimal("100"):
        return "low"
    if amount < Decimal("1000"):
        return "medium"
    return "high"


def clean_record(raw: dict[str, str], stats: TransformStats) -> CleanTransaction | None:
    transaction_id = (raw.get("transaction_id") or "").strip()
    account_id = (raw.get("account_id") or "").strip()
    description = (raw.get("description") or "").strip()
    channel = (raw.get("channel") or "").strip().upper() or "UNKNOWN"

    if not transaction_id:
        stats.record_drop("missing_transaction_id")
        return None
    if not account_id:
        stats.record_drop("missing_account_id")
        return None

    amount = parse_amount(raw.get("amount"))
    if amount is None:
        stats.record_drop("invalid_amount")
        return None

    status = normalize_status(raw.get("status"))
    if status is None:
        stats.record_drop("unknown_status")
        return None

    timestamp = parse_timestamp(raw.get("timestamp"))
    if timestamp is None:
        stats.record_drop("invalid_timestamp")
        return None

    return CleanTransaction(
        transaction_id=transaction_id,
        account_id=account_id,
        amount=str(amount),
        status=status,
        channel=channel,
        timestamp=timestamp.isoformat(),
        description=description,
        hour_of_day=timestamp.hour,
        day_of_week=timestamp.weekday(),
        is_weekend=timestamp.weekday() >= 5,
        amount_bucket=amount_bucket(amount),
    )


def extract(input_path: Path) -> list[dict[str, str]]:
    with input_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def transform(raw_rows: list[dict[str, str]], stats: TransformStats) -> list[CleanTransaction]:
    seen_ids: set[str] = set()
    clean_rows: list[CleanTransaction] = []

    for raw in raw_rows:
        stats.rows_read += 1
        record = clean_record(raw, stats)
        if record is None:
            continue
        if record.transaction_id in seen_ids:
            stats.duplicates_skipped += 1
            continue
        seen_ids.add(record.transaction_id)
        clean_rows.append(record)
        stats.rows_clean += 1

    return clean_rows


def load(clean_rows: list[CleanTransaction], output_dir: Path, stats: TransformStats) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON Lines: un objeto por línea, sin envolver todo en un único array
    # gigante. Es el formato que consumen la mayoría de pipelines de
    # análisis/ML por lotes (se puede leer en streaming, agregar filas
    # nuevas sin reescribir el archivo, y cada línea es independiente).
    dataset_path = output_dir / "transactions_clean.jsonl"
    with dataset_path.open("w", encoding="utf-8") as f:
        for record in clean_rows:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    report_path = output_dir / "transform_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(stats.as_dict(), f, ensure_ascii=False, indent=2)

    logger.info("Dataset limpio escrito en %s", dataset_path)
    logger.info("Reporte de transformación escrito en %s", report_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).parent / "raw_transactions_sample.csv",
        help="CSV crudo de entrada (extracto simulado de Bancs)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "output",
        help="Directorio donde escribir el dataset limpio y el reporte",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    stats = TransformStats()
    raw_rows = extract(args.input)
    clean_rows = transform(raw_rows, stats)
    load(clean_rows, args.output, stats)

    logger.info("Resumen: %s", stats.as_dict())


if __name__ == "__main__":
    main()
