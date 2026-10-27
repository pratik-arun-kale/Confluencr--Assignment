import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import (
    BalanceLimitExceededError,
    DuplicateTransactionError,
    InsufficientBalanceError,
    ReferenceIdConflictError,
)
from app.models import MAX_BALANCE, Account, Transaction, TransactionType
from app.schemas import TransactionCreate, TransactionOut
from app.services.accounts import lock_accounts

UNIQUE_REFERENCE_CONSTRAINT = "uq_transactions_account_reference"


def apply_movement(account: Account, type_: TransactionType, amount: Decimal) -> None:
    """Change a (locked) account's balance, enforcing the ledger's balance rules."""
    if type_ is TransactionType.DEBIT:
        if account.balance < amount:
            raise InsufficientBalanceError(
                f"Insufficient balance: available {account.balance:.2f}, requested {amount:.2f}",
                {
                    "account_id": str(account.id),
                    "available_balance": f"{account.balance:.2f}",
                    "requested_amount": f"{amount:.2f}",
                },
            )
        account.balance -= amount
    else:
        if account.balance + amount > MAX_BALANCE:
            raise BalanceLimitExceededError(
                f"Credit would exceed the maximum balance of {MAX_BALANCE}",
                {"account_id": str(account.id)},
            )
        account.balance += amount


def create_transaction(db: Session, account_id: uuid.UUID, data: TransactionCreate) -> Transaction:
    """Post a credit/debit and update the balance as one atomic unit.

    1. Lock the account row (FOR UPDATE): concurrent writers on this account now queue,
       so the duplicate check and the balance check below cannot race.
    2. Reject a reused reference_id (no new row, balance untouched).
    3. Validate and apply the balance change.
    4. Insert the ledger entry. Commit; any exception rolls back everything.
    """
    try:
        with db.begin():
            account = lock_accounts(db, account_id)[account_id]

            existing = _find_by_reference(db, account_id, data.reference_id)
            if existing is not None:
                raise _duplicate_error(existing, data)

            apply_movement(account, data.type, data.amount)
            transaction = Transaction(
                account_id=account_id,
                type=data.type.value,
                amount=data.amount,
                description=data.description,
                reference_id=data.reference_id,
            )
            db.add(transaction)
        return transaction
    except IntegrityError as exc:
        # Safety net: the row lock makes this unreachable in practice, but the unique
        # constraint is the final authority, so map a violation to the same 409.
        if _constraint_name(exc) != UNIQUE_REFERENCE_CONSTRAINT:
            raise
        existing = _find_by_reference(db, account_id, data.reference_id)
        if existing is None:
            raise
        raise _duplicate_error(existing, data) from exc


def _find_by_reference(
    db: Session, account_id: uuid.UUID, reference_id: str
) -> Transaction | None:
    return db.scalar(
        select(Transaction).where(
            Transaction.account_id == account_id, Transaction.reference_id == reference_id
        )
    )


def _duplicate_error(
    existing: Transaction, data: TransactionCreate
) -> DuplicateTransactionError | ReferenceIdConflictError:
    same_payload = (
        existing.type == data.type.value
        and existing.amount == data.amount
        and existing.description == data.description
    )
    if same_payload:
        # A retry of a request that already succeeded: point the client at the original.
        return DuplicateTransactionError(
            f"Transaction with reference_id '{existing.reference_id}' already exists",
            {"existing_transaction": TransactionOut.model_validate(existing).model_dump(mode="json")},
        )
    return ReferenceIdConflictError(
        f"reference_id '{existing.reference_id}' is already used by a different transaction",
        {"reference_id": existing.reference_id, "existing_transaction_id": str(existing.id)},
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)
