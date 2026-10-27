import uuid

import pytest
from sqlalchemy import text

from tests.conftest import API


@pytest.fixture
def accounts(create_account, post_txn):
    """The brief's example: A = 100, B = 50."""
    a, b = create_account("A"), create_account("B")
    post_txn(a["id"], "credit", "100")
    post_txn(b["id"], "credit", "50")
    return a["id"], b["id"]


@pytest.fixture
def transfer(client):
    def _transfer(from_id, to_id, amount, reference_id="TRF-1", **extra):
        payload = {
            "from_account_id": from_id,
            "to_account_id": to_id,
            "amount": amount,
            "reference_id": reference_id,
            **extra,
        }
        return client.post(f"{API}/transfers", json=payload)

    return _transfer


def count(engine, table):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()


def test_transfer_moves_money(accounts, transfer, balance_of):
    a, b = accounts

    response = transfer(a, b, "30", description="Rent")

    assert response.status_code == 201
    assert balance_of(a) == "70.00"
    assert balance_of(b) == "80.00"


def test_transfer_response_includes_both_legs(accounts, transfer):
    a, b = accounts

    body = transfer(a, b, "30", description="Rent").json()

    assert body["from_account_id"] == a
    assert body["to_account_id"] == b
    assert body["amount"] == "30.00"
    assert body["reference_id"] == "TRF-1"
    debit, credit = body["debit_transaction"], body["credit_transaction"]
    assert (debit["account_id"], debit["type"], debit["amount"]) == (a, "debit", "30.00")
    assert (credit["account_id"], credit["type"], credit["amount"]) == (b, "credit", "30.00")
    assert debit["transfer_id"] == credit["transfer_id"] == body["id"]
    assert debit["description"] == credit["description"] == "Rent"


def test_transfer_legs_appear_in_both_histories(client, accounts, transfer):
    a, b = accounts
    transfer_id = transfer(a, b, "30").json()["id"]

    for account_id, type_ in ((a, "debit"), (b, "credit")):
        latest = client.get(f"{API}/accounts/{account_id}/transactions").json()["data"][0]
        assert latest["transfer_id"] == transfer_id
        assert latest["type"] == type_


def test_insufficient_balance_rolls_back_everything(engine, accounts, transfer, balance_of):
    a, b = accounts
    before = count(engine, "transactions")

    response = transfer(a, b, "100.01")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_BALANCE"
    assert balance_of(a) == "100.00"
    assert balance_of(b) == "50.00"
    assert count(engine, "transactions") == before
    assert count(engine, "transfers") == 0


def test_transfer_to_same_account_is_rejected(accounts, transfer, balance_of):
    a, _ = accounts

    response = transfer(a, a, "10")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SAME_ACCOUNT_TRANSFER"
    assert balance_of(a) == "100.00"


@pytest.mark.parametrize("missing_side", ["from", "to"])
def test_missing_account_returns_404_and_changes_nothing(
    engine, accounts, transfer, balance_of, missing_side
):
    a, _ = accounts
    ghost = str(uuid.uuid4())
    from_id, to_id = (ghost, a) if missing_side == "from" else (a, ghost)

    response = transfer(from_id, to_id, "10")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "ACCOUNT_NOT_FOUND"
    assert error["details"]["account_id"] == ghost
    assert balance_of(a) == "100.00"
    assert count(engine, "transfers") == 0


def test_duplicate_transfer_returns_409_with_original(accounts, transfer, balance_of):
    a, b = accounts
    first = transfer(a, b, "30")

    retry = transfer(a, b, "30")

    assert retry.status_code == 409
    error = retry.json()["error"]
    assert error["code"] == "DUPLICATE_TRANSFER"
    assert error["details"]["existing_transfer"] == first.json()
    assert balance_of(a) == "70.00"
    assert balance_of(b) == "80.00"


def test_reused_transfer_reference_with_different_payload_conflicts(accounts, transfer, balance_of):
    a, b = accounts
    transfer(a, b, "30")

    response = transfer(a, b, "40")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REFERENCE_ID_CONFLICT"
    assert balance_of(a) == "70.00"


def test_same_transfer_reference_allowed_from_different_sources(accounts, transfer, balance_of):
    a, b = accounts

    assert transfer(a, b, "10", reference_id="SAME").status_code == 201
    assert transfer(b, a, "5", reference_id="SAME").status_code == 201
    assert balance_of(a) == "95.00"
    assert balance_of(b) == "55.00"


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"amount": "0"}, "amount"),
        ({"amount": "-1"}, "amount"),
        ({"amount": "1.001"}, "amount"),
        ({"reference_id": ""}, "reference_id"),
        ({"from_account_id": "nope"}, "from_account_id"),
    ],
)
def test_transfer_validation(client, accounts, override, field):
    a, b = accounts
    payload = {"from_account_id": a, "to_account_id": b, "amount": "10", "reference_id": "R"}

    response = client.post(f"{API}/transfers", json={**payload, **override})

    assert response.status_code == 400
    assert response.json()["error"]["details"][0]["field"] == field
