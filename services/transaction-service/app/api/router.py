"""
Router HTTP.

Deliberadamente delgado: solo traduce Request -> Command, invoca el
caso de uso, y traduce Result -> Response. Cero lógica de negocio
acá — si alguien quisiera exponer esto mismo por gRPC, este archivo
es lo único que cambiaría.
"""

from fastapi import APIRouter, Depends

from app.core.logging import trace_id_var
from app.application.dto import ProcessTransactionCommand
from app.application.use_cases import ProcessTransactionUseCase
from app.api.schemas import TransactionRequest, TransactionResponse
from app.api.dependencies import get_process_transaction_use_case

router = APIRouter(prefix="/api", tags=["transactions"])


@router.post("/transactions", response_model=TransactionResponse, status_code=201)
async def create_transaction(
    body: TransactionRequest,
    use_case: ProcessTransactionUseCase = Depends(get_process_transaction_use_case),
):
    command = ProcessTransactionCommand(
        idempotency_key=body.idempotency_key,
        account_id=body.account_id,
        amount=body.amount,
        trace_id=trace_id_var.get(),
    )
    result = await use_case.execute(command)
    return TransactionResponse(
        transaction_id=result.transaction_id,
        status=result.status,
        new_balance=result.new_balance,
        trace_id=result.trace_id,
        was_replayed=result.was_replayed,
    )