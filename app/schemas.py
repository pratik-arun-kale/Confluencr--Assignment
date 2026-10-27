"""Request/response models. Money is exact Decimal on input and a 2-dp string on output."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
)

from app.models import TransactionType

MAX_AMOUNT = Decimal("1000000000.00")  # per-operation cap: 1 billion
CENT = Decimal("0.01")


def _to_cents(value: Decimal) -> Decimal:
    # Normalise 50 / 50.5 -> 50.00 / 50.50. Exact: >2 dp is already rejected by Field.
    return value.quantize(CENT)


AmountIn = Annotated[
    Decimal,
    Field(
        gt=0,
        le=MAX_AMOUNT,
        decimal_places=2,
        description="Positive amount with at most 2 decimal places",
        examples=["50.00"],
    ),
    AfterValidator(_to_cents),
]
MoneyOut = Annotated[
    Decimal, PlainSerializer(lambda v: f"{v:.2f}", return_type=str, when_used="json")
]

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ReferenceId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]


class StrictInput(BaseModel):
    # Unknown fields (e.g. "balance" on account creation) are rejected, not silently ignored.
    model_config = ConfigDict(extra="forbid")


class ORMOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Accounts ----------


class AccountCreate(StrictInput):
    name: Name = Field(examples=["Pratik Kale"])


class AccountOut(ORMOutput):
    id: uuid.UUID
    name: str
    balance: MoneyOut
    created_at: datetime


class BalanceOut(BaseModel):
    account_id: uuid.UUID
    balance: MoneyOut


# ---------- Transactions ----------


class TransactionCreate(StrictInput):
    type: TransactionType
    amount: AmountIn
    description: Description | None = Field(default=None, examples=["Salary for October"])
    reference_id: ReferenceId = Field(examples=["INV-2026-0001"])


class TransactionOut(ORMOutput):
    id: uuid.UUID
    account_id: uuid.UUID
    type: TransactionType
    amount: MoneyOut
    description: str | None
    reference_id: str
    transfer_id: uuid.UUID | None
    created_at: datetime


class PaginationMeta(BaseModel):
    page: int
    limit: int
    total_records: int
    total_pages: int


class TransactionPage(BaseModel):
    data: list[TransactionOut]
    pagination: PaginationMeta


# ---------- Transfers ----------


class TransferCreate(StrictInput):
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: AmountIn
    description: Description | None = Field(default=None, examples=["Rent share"])
    reference_id: ReferenceId = Field(examples=["TRF-2026-0001"])


class TransferOut(ORMOutput):
    id: uuid.UUID
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: MoneyOut
    description: str | None
    reference_id: str
    created_at: datetime
    debit_transaction: TransactionOut
    credit_transaction: TransactionOut


# ---------- Errors / health (for OpenAPI docs) ----------


class ErrorDetail(BaseModel):
    code: str = Field(examples=["INSUFFICIENT_BALANCE"])
    message: str
    details: Any = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthOut(BaseModel):
    status: str
    database: str
