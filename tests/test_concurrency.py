"""Race-condition tests. Each test releases N threads at the same instant (Barrier) against
the real database, each with its own session/connection, exactly like concurrent requests."""

import threading
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.errors import AppError
from app.models import TransactionType
from app.schemas import TransactionCreate, TransferCreate
from app.services.transactions import create_transaction
from app.services.transfers import create_transfer


def run_concurrently(jobs: list[Callable[[Session], Any]]) -> Counter[str]:
    """Run all jobs at once; return a count of outcomes ('ok' or the AppError code)."""
    barrier = threading.Barrier(len(jobs))

    def run(job: Callable[[Session], Any]) -> str:
        barrier.wait()
        session = SessionLocal()
        try:
            job(session)
            return "ok"
        except AppError as exc:
            return exc.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return Counter(pool.map(run, jobs))


def txn(account_id: str, type_: str, amount: str, reference_id: str | None = None):
    data = TransactionCreate(
        type=TransactionType(type_),
        amount=Decimal(amount),
        reference_id=reference_id or uuid.uuid4().hex,
    )
    return lambda session: create_transaction(session, uuid.UUID(account_id), data)


def transfer(from_id: str, to_id: str, amount: str):
    data = TransferCreate(
        from_account_id=uuid.UUID(from_id),
        to_account_id=uuid.UUID(to_id),
        amount=Decimal(amount),
        reference_id=uuid.uuid4().hex,
    )
    return lambda session: create_transfer(session, data)


@pytest.fixture
def funded(create_account, post_txn):
    def _funded(amount: str = "100") -> str:
        account_id: str = create_account()["id"]
        post_txn(account_id, "credit", amount, reference_id="seed")
        return account_id

    return _funded


def test_concurrent_debits_never_overdraw(funded, balance_of):
    account = funded("100")

    outcomes = run_concurrently([txn(account, "debit", "10") for _ in range(20)])

    assert outcomes == {"ok": 10, "INSUFFICIENT_BALANCE": 10}
    assert balance_of(account) == "0.00"


def test_concurrent_mixed_credits_and_debits_lose_no_updates(funded, balance_of):
    account = funded("100")
    jobs = [txn(account, "credit", "5") for _ in range(15)] + [
        txn(account, "debit", "3") for _ in range(15)
    ]

    outcomes = run_concurrently(jobs)

    assert outcomes == {"ok": 30}
    assert balance_of(account) == f"{100 + 15 * 5 - 15 * 3:.2f}"


def test_concurrent_duplicate_reference_creates_exactly_one(engine, funded, balance_of):
    account = funded("0")

    outcomes = run_concurrently([txn(account, "credit", "10", "SAME-REF") for _ in range(20)])

    assert outcomes == {"ok": 1, "DUPLICATE_TRANSACTION": 19}
    assert balance_of(account) == "10.00"
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT count(*) FROM transactions WHERE reference_id = 'SAME-REF'")
        ).scalar()
    assert rows == 1


def test_opposing_transfers_do_not_deadlock_and_conserve_money(funded, balance_of):
    a, b = funded("100"), funded("100")
    jobs = [transfer(a, b, "1") for _ in range(25)] + [transfer(b, a, "1") for _ in range(25)]

    outcomes = run_concurrently(jobs)

    # A deadlock would surface as a DB error (not an AppError) and fail the test.
    assert outcomes == {"ok": 50}
    assert balance_of(a) == "100.00"
    assert balance_of(b) == "100.00"


def test_concurrent_transfers_from_one_source_never_overdraw(funded, balance_of):
    source, x, y = funded("50"), funded("0"), funded("0")
    jobs = [transfer(source, x, "10") for _ in range(5)] + [
        transfer(source, y, "10") for _ in range(5)
    ]

    outcomes = run_concurrently(jobs)

    assert outcomes == {"ok": 5, "INSUFFICIENT_BALANCE": 5}
    assert balance_of(source) == "0.00"
    total = Decimal(balance_of(source)) + Decimal(balance_of(x)) + Decimal(balance_of(y))
    assert total == Decimal("50.00")
