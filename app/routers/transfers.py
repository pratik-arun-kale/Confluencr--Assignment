from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.common import error_responses
from app.schemas import TransferCreate, TransferOut
from app.services import transfers as transfer_service

router = APIRouter(prefix="/transfers", tags=["Transfers"])


@router.post(
    "",
    response_model=TransferOut,
    status_code=status.HTTP_201_CREATED,
    summary="Transfer money between accounts",
    description=(
        "Atomically debits the source and credits the destination; both legs appear in the "
        "accounts' histories (linked by `transfer_id`). Either everything is written or "
        "nothing is. `reference_id` is unique per source account."
    ),
    responses=error_responses(400, 404, 409, 422, 503),
)
def create_transfer(payload: TransferCreate, db: Session = Depends(get_db)) -> TransferOut:
    return transfer_service.create_transfer(db, payload)
