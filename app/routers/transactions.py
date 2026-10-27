import math
import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Transaction, TransactionType
from app.routers.common import error_responses
from app.schemas import PaginationMeta, TransactionCreate, TransactionOut, TransactionPage
from app.services import transactions as transaction_service

MAX_PAGE_SIZE = 100

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
    responses=error_responses(400, 404, 409, 422, 503),
)
def create_transaction(
    account_id: uuid.UUID, payload: TransactionCreate, db: Session = Depends(get_db)
) -> Transaction:
    return transaction_service.create_transaction(db, account_id, payload)


@router.get(
    "",
    response_model=TransactionPage,
    summary="List an account's transactions",
    description=(
        "Latest first (by `created_at`). Optional `type` filter; without it both credits and "
        "debits are returned. Paginated with `page` (>= 1) and `limit` (1-100)."
    ),
    responses=error_responses(400, 404),
)
def list_transactions(
    account_id: uuid.UUID,
    type_: TransactionType | None = Query(
        default=None, alias="type", description="Filter by transaction type"
    ),
    page: int = Query(default=1, ge=1, description="1-based page number"),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE, description="Page size"),
    db: Session = Depends(get_db),
) -> TransactionPage:
    items, total = transaction_service.list_transactions(db, account_id, type_, page, limit)
    return TransactionPage(
        data=[TransactionOut.model_validate(item) for item in items],
        pagination=PaginationMeta(
            page=page,
            limit=limit,
            total_records=total,
            total_pages=math.ceil(total / limit),
        ),
    )
