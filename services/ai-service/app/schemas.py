"""Contratos HTTP del servicio de IA."""

from pydantic import BaseModel, Field


class TransactionFeatures(BaseModel):
    transaction_id: str
    account_id: str
    amount: float = Field(gt=0)
    hour_of_day: int = Field(ge=0, le=23)
    day_of_week: int = Field(ge=0, le=6)
    is_weekend: bool


class RiskScoreResponse(BaseModel):
    transaction_id: str
    risk_score: float
    risk_level: str
    model_version: str
