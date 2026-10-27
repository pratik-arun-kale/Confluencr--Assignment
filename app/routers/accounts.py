import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account
from app.routers.common import error_responses
from app.schemas import AccountCreate, AccountOut, BalanceOut
from app.services import accounts as account_service

router = APIRouter(prefix="/accounts", tags=["Accounts"])


@router.post(
    "",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    description="Creates an account with a zero balance. Supplying `balance` is rejected.",
    responses=error_responses(400),
)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)) -> Account:
    return account_service.create_account(db, payload)


@router.get(
    "/{account_id}",
    response_model=AccountOut,
    summary="Get an account",
    responses=error_responses(400, 404),
)
def get_account(account_id: uuid.UUID, db: Session = Depends(get_db)) -> Account:
    return account_service.get_account(db, account_id)


@router.get(
    "/{account_id}/balance",
    response_model=BalanceOut,
    summary="Get an account's current balance",
    responses=error_responses(400, 404),
)
def get_balance(account_id: uuid.UUID, db: Session = Depends(get_db)) -> BalanceOut:
    account = account_service.get_account(db, account_id)
    return BalanceOut(account_id=account.id, balance=account.balance)
