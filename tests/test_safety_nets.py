"""Paths that are unreachable in normal operation but must still behave correctly.

The application checks for a duplicate reference_id before inserting. If that check were
ever bypassed (bug, refactor, race we didn't foresee), the UNIQUE constraint must still
stop the duplicate and the API must still answer 409 - never 500, never a double entry.
"""

from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError

from app.services import transactions as transaction_service
from app.services import transfers as transfer_service
from tests.conftest import API


def _skip_first_lookup(real):
    """Make the first pre-insert duplicate lookup miss, as if it raced with another writer."""
    calls = {"n": 0}

    def lookup(*args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*args, **kwargs)

    return lookup


def test_db_constraint_catches_duplicate_transaction(create_account, post_txn, balance_of):
    account = create_account()["id"]
    first = post_txn(account, "credit", "10", reference_id="R")

    real = transaction_service._find_by_reference
    with patch.object(transaction_service, "_find_by_reference", _skip_first_lookup(real)):
        retry = post_txn(account, "credit", "10", reference_id="R")

    assert retry.status_code == 409
    assert retry.json()["error"]["details"]["existing_transaction"] == first.json()
    assert balance_of(account) == "10.00"


def test_db_constraint_catches_duplicate_transfer(client, create_account, post_txn, balance_of):
    a, b = create_account("A")["id"], create_account("B")["id"]
    post_txn(a, "credit", "100")
    payload = {"from_account_id": a, "to_account_id": b, "amount": "10", "reference_id": "T"}
    first = client.post(f"{API}/transfers", json=payload)

    real = transfer_service._find_by_reference
    with patch.object(transfer_service, "_find_by_reference", _skip_first_lookup(real)):
        retry = client.post(f"{API}/transfers", json=payload)

    assert retry.status_code == 409
    assert retry.json()["error"]["details"]["existing_transfer"] == first.json()
    assert balance_of(a) == "90.00"
    assert balance_of(b) == "10.00"


def test_health_reports_database_down(client):
    with patch("sqlalchemy.orm.Session.execute", side_effect=SQLAlchemyError("down")):
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "down"}
