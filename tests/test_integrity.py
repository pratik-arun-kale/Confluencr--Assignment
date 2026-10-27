"""Defence in depth: even raw SQL that bypasses the API cannot break ledger integrity."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError


@pytest.fixture
def ledger(create_account, post_txn, client):
    a, b = create_account("A"), create_account("B")
    txn = post_txn(a["id"], "credit", "100", reference_id="R1").json()
    transfer = client.post(
        "/api/v1/transfers",
        json={"from_account_id": a["id"], "to_account_id": b["id"], "amount": "10", "reference_id": "T1"},
    ).json()
    return {"a": a["id"], "b": b["id"], "txn": txn["id"], "transfer": transfer["id"]}


def execute(engine, sql, **params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE transactions SET amount = 1 WHERE id = :id",
        "UPDATE transactions SET type = 'debit' WHERE id = :id",
        "UPDATE transactions SET created_at = now() - interval '1 day' WHERE id = :id",
        "DELETE FROM transactions WHERE id = :id",
    ],
)
def test_transactions_are_immutable_in_the_database(engine, ledger, sql):
    with pytest.raises(DBAPIError, match="ledger records are immutable"):
        execute(engine, sql, id=ledger["txn"])


@pytest.mark.parametrize(
    "sql",
    ["UPDATE transfers SET amount = 1 WHERE id = :id", "DELETE FROM transfers WHERE id = :id"],
)
def test_transfers_are_immutable_in_the_database(engine, ledger, sql):
    with pytest.raises(DBAPIError, match="ledger records are immutable"):
        execute(engine, sql, id=ledger["transfer"])


def test_balance_cannot_go_negative(engine, ledger):
    with pytest.raises(IntegrityError, match="ck_accounts_balance_non_negative"):
        execute(engine, "UPDATE accounts SET balance = -0.01 WHERE id = :id", id=ledger["a"])


def test_blank_account_name_rejected(engine):
    with pytest.raises(IntegrityError, match="ck_accounts_name_not_blank"):
        execute(engine, "INSERT INTO accounts (id, name) VALUES (:id, '  ')", id=uuid.uuid4())


INSERT_TXN = (
    "INSERT INTO transactions (id, account_id, type, amount, reference_id) "
    "VALUES (:id, :account, :type, :amount, :ref)"
)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"ref": "R1"}, "uq_transactions_account_reference"),
        ({"amount": 0}, "ck_transactions_amount_positive"),
        ({"amount": -5}, "ck_transactions_amount_positive"),
        ({"type": "refund"}, "ck_transactions_type"),
        ({"ref": " "}, "ck_transactions_reference_not_blank"),
        ({"account": uuid.uuid4()}, "transactions_account_id_fkey"),
    ],
)
def test_transaction_constraints(engine, ledger, overrides, constraint):
    params = {"id": uuid.uuid4(), "account": ledger["a"], "type": "credit", "amount": 1, "ref": "new"}

    with pytest.raises(IntegrityError, match=constraint):
        execute(engine, INSERT_TXN, **{**params, **overrides})


def test_transfer_between_same_account_rejected_by_db(engine, ledger):
    with pytest.raises(IntegrityError, match="ck_transfers_distinct_accounts"):
        execute(
            engine,
            "INSERT INTO transfers (id, from_account_id, to_account_id, amount, reference_id) "
            "VALUES (:id, :a, :a, 1, 'x')",
            id=uuid.uuid4(),
            a=ledger["a"],
        )


def test_stored_balance_matches_ledger(engine, ledger):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT a.id, a.balance,
                       COALESCE(SUM(CASE t.type WHEN 'credit' THEN t.amount ELSE -t.amount END), 0)
                FROM accounts a LEFT JOIN transactions t ON t.account_id = a.id
                GROUP BY a.id, a.balance
                """
            )
        ).all()
    assert rows
    for _, balance, ledger_sum in rows:
        assert balance == ledger_sum
