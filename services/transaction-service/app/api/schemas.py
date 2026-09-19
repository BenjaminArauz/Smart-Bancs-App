from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field


class TransactionRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=255)
    account_id: UUID
    amount: Decimal = Field(..., gt=0)


class TransactionResponse(BaseModel):
    transaction_id: UUID
    status: str
    new_balance: Decimal
    trace_id: str
    was_replayed: bool


class ErrorResponse(BaseModel):
    error: str
    message: str
    trace_id: str
