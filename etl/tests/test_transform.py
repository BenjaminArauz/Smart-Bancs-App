import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transform import (  # noqa: E402
    TransformStats,
    amount_bucket,
    clean_record,
    normalize_status,
    parse_amount,
    parse_timestamp,
    transform,
)


def test_parse_amount_strips_currency_and_thousands_separators():
    assert parse_amount("$1,250.00") == Decimal("1250.00")
    assert parse_amount(" 500.00") == Decimal("500.00")


def test_parse_amount_rejects_invalid_values():
    assert parse_amount("N/A") is None
    assert parse_amount("") is None
    assert parse_amount(None) is None
    assert parse_amount("-10.00") is None


def test_normalize_status_maps_legacy_codes():
    assert normalize_status("COMPLETADA") == "completed"
    assert normalize_status("  ok ") == "completed"
    assert normalize_status("C") == "completed"
    assert normalize_status("ERROR") == "failed"
    assert normalize_status("no-existe") is None


def test_parse_timestamp_accepts_multiple_formats():
    assert parse_timestamp("2026-09-15T09:02:44Z") is not None
    assert parse_timestamp("15/09/2026") is not None
    assert parse_timestamp("2026-09-15 08:23:11") is not None
    assert parse_timestamp("") is None
    assert parse_timestamp(None) is None


def test_amount_bucket_thresholds():
    assert amount_bucket(Decimal("50")) == "low"
    assert amount_bucket(Decimal("500")) == "medium"
    assert amount_bucket(Decimal("5000")) == "high"


def test_clean_record_drops_row_with_missing_amount():
    stats = TransformStats()
    raw = {
        "transaction_id": "TXN-1",
        "account_id": "acc-1",
        "amount": "",
        "status": "completada",
        "channel": "app",
        "timestamp": "2026-09-15 08:23:11",
        "description": "",
    }
    assert clean_record(raw, stats) is None
    assert stats.drop_reasons["invalid_amount"] == 1


def test_transform_deduplicates_by_transaction_id():
    stats = TransformStats()
    raw_rows = [
        {
            "transaction_id": "TXN-1",
            "account_id": "acc-1",
            "amount": "100.00",
            "status": "ok",
            "channel": "app",
            "timestamp": "2026-09-15 08:23:11",
            "description": "",
        },
        {
            "transaction_id": "TXN-1",
            "account_id": "acc-1",
            "amount": "100.00",
            "status": "ok",
            "channel": "app",
            "timestamp": "2026-09-15 08:23:11",
            "description": "",
        },
    ]
    clean_rows = transform(raw_rows, stats)
    assert len(clean_rows) == 1
    assert stats.duplicates_skipped == 1
