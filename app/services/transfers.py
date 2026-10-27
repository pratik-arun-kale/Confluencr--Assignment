import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import DuplicateTransferError, ReferenceIdConflictError, SameAccountTransferError
from app.models import Transaction, TransactionType, Transfer
from app.schemas import TransactionOut, TransferCreate, TransferOut
from app.services.accounts import lock_accounts
from app.services.transactions import apply_movement

UNIQUE_REFERENCE_CONSTRAINT = "uq_transfers_from_reference"


def create_transfer(db: Session, data: TransferCreate) -> TransferOut:
    """Move money between two accounts: debit source, credit destination, all-or-nothing.

    Both account rows are locked in ascending id order (see lock_accounts), so opposing
    transfers cannot deadlock. Any failure (missing account, insufficient funds, duplicate
    reference) raises inside the transaction, rolling back every write: a partial transfer
    is impossible.
    """
    if data.from_account_id == data.to_account_id:
        raise SameAccountTransferError(
            "Source and destination accounts must be different",
            {"account_id": str(data.from_account_id)},
        )

    try:
        with db.begin():
            accounts = lock_accounts(db, data.from_account_id, data.to_account_id)
            source, destination = accounts[data.from_account_id], accounts[data.to_account_id]

            existing = _find_by_reference(db, data.from_account_id, data.reference_id)
            if existing is not None:
                raise _duplicate_error(db, existing, data)

            apply_movement(source, TransactionType.DEBIT, data.amount)
            apply_movement(destination, TransactionType.CREDIT, data.amount)

            transfer = Transfer(
                id=uuid.uuid4(),
                from_account_id=source.id,
                to_account_id=destination.id,
                amount=data.amount,
                reference_id=data.reference_id,
                description=data.description,
            )
            db.add(transfer)
            db.flush()  # transfer row must exist before the legs reference it

            # Each leg is an ordinary ledger entry, so it shows up in both histories.
            leg_reference = f"TRF-{transfer.id}"
            debit, credit = (
                Transaction(
                    account_id=account_id,
                    type=type_.value,
                    amount=data.amount,
                    description=data.description,
                    reference_id=leg_reference,
                    transfer_id=transfer.id,
                )
                for account_id, type_ in (
                    (source.id, TransactionType.DEBIT),
                    (destination.id, TransactionType.CREDIT),
                )
            )
            db.add_all([debit, credit])
        return _to_out(transfer, debit, credit)
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        if getattr(diag, "constraint_name", None) != UNIQUE_REFERENCE_CONSTRAINT:
            raise
        existing = _find_by_reference(db, data.from_account_id, data.reference_id)
        if existing is None:
            raise
        raise _duplicate_error(db, existing, data) from exc


def _find_by_reference(db: Session, from_account_id: uuid.UUID, reference_id: str) -> Transfer | None:
    return db.scalar(
        select(Transfer).where(
            Transfer.from_account_id == from_account_id, Transfer.reference_id == reference_id
        )
    )


def _duplicate_error(
    db: Session, existing: Transfer, data: TransferCreate
) -> DuplicateTransferError | ReferenceIdConflictError:
    same_payload = (
        existing.to_account_id == data.to_account_id
        and existing.amount == data.amount
        and existing.description == data.description
    )
    if not same_payload:
        return ReferenceIdConflictError(
            f"reference_id '{existing.reference_id}' is already used by a different transfer",
            {"reference_id": existing.reference_id, "existing_transfer_id": str(existing.id)},
        )
    legs = {
        leg.account_id: leg
        for leg in db.scalars(select(Transaction).where(Transaction.transfer_id == existing.id))
    }
    original = _to_out(existing, legs[existing.from_account_id], legs[existing.to_account_id])
    return DuplicateTransferError(
        f"Transfer with reference_id '{existing.reference_id}' already exists",
        {"existing_transfer": original.model_dump(mode="json")},
    )


def _to_out(transfer: Transfer, debit: Transaction, credit: Transaction) -> TransferOut:
    return TransferOut(
        id=transfer.id,
        from_account_id=transfer.from_account_id,
        to_account_id=transfer.to_account_id,
        amount=transfer.amount,
        description=transfer.description,
        reference_id=transfer.reference_id,
        created_at=transfer.created_at,
        debit_transaction=TransactionOut.model_validate(debit),
        credit_transaction=TransactionOut.model_validate(credit),
    )
