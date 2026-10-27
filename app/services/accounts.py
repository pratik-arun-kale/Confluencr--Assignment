import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AccountNotFoundError
from app.models import Account
from app.schemas import AccountCreate


def create_account(db: Session, data: AccountCreate) -> Account:
    # balance is never taken from the client: the DB default (0) applies.
    account = Account(name=data.name)
    with db.begin():
        db.add(account)
    return account


def get_account(db: Session, account_id: uuid.UUID) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise AccountNotFoundError(account_id)
    return account


def lock_accounts(db: Session, *account_ids: uuid.UUID) -> dict[uuid.UUID, Account]:
    """SELECT ... FOR UPDATE on the given accounts, always in ascending id order.

    The row lock serialises concurrent writers on the same account until the surrounding
    transaction ends. A consistent lock order means two transfers A->B and B->A can never
    each hold one lock while waiting for the other (no deadlock).
    Must be called inside an open transaction.
    """
    ordered = sorted(set(account_ids))
    rows = db.scalars(
        select(Account).where(Account.id.in_(ordered)).order_by(Account.id).with_for_update()
    ).all()
    found = {account.id: account for account in rows}
    for account_id in account_ids:
        if account_id not in found:
            raise AccountNotFoundError(account_id)
    return found
