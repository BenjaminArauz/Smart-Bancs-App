"""Tests del modelo de riesgo: entrena un modelo efímero y valida el contrato de scoring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.model import RiskModel


def test_score_returns_bounded_risk_score(tmp_path):
    import train_model

    model_path = tmp_path / "risk_model.joblib"
    original_path = train_model.MODEL_PATH
    train_model.MODEL_PATH = model_path
    try:
        train_model.train()
    finally:
        train_model.MODEL_PATH = original_path

    model = RiskModel(model_path=model_path)

    risk_score, risk_level = model.score(
        {"amount": 50.0, "hour_of_day": 14, "day_of_week": 2, "is_weekend": 0}
    )
    assert 0.0 <= risk_score <= 1.0
    assert risk_level in {"low", "medium", "high"}


def test_anomalous_transaction_scores_higher_than_typical(tmp_path):
    import train_model

    model_path = tmp_path / "risk_model.joblib"
    original_path = train_model.MODEL_PATH
    train_model.MODEL_PATH = model_path
    try:
        train_model.train()
    finally:
        train_model.MODEL_PATH = original_path

    model = RiskModel(model_path=model_path)

    typical_score, _ = model.score({"amount": 45.0, "hour_of_day": 13, "day_of_week": 1, "is_weekend": 0})
    atypical_score, atypical_level = model.score(
        {"amount": 5000.0, "hour_of_day": 3, "day_of_week": 6, "is_weekend": 1}
    )

    assert atypical_score > typical_score
    assert atypical_level in {"medium", "high"}
