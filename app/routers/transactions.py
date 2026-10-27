import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Transaction
from app.routers.common import error_responses
from app.schemas import TransactionCreate, TransactionOut
from app.services import transactions as transaction_service

router = APIRouter(prefix="/accounts/{account_id}/transactions", tags=["Transactions"])


@router.post(
    "",
    response_model=TransactionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Post a credit or debit",
    description=(
        "Atomically records the transaction and updates the balance. "
        "A debit larger than the balance is rejected (422) and nothing changes. "
        "`reference_id` is unique per account: repeating it returns 409 and nothing changes "
        "(`DUPLICATE_TRANSACTION` with the original for an identical retry, "
        "`REFERENCE_ID_CONFLICT` if the payload differs)."
    ),
    responses=error_responses(400, 404, 409, 422),
)
def create_transaction(
    account_id: uuid.UUID, payload: TransactionCreate, db: Session = Depends(get_db)
) -> Transaction:
    return transaction_service.create_transaction(db, account_id, payload)
