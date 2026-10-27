import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.errors import BalanceLimitExceededError
from app.models import MAX_BALANCE, Account, TransactionType
from app.services.transactions import apply_movement
from tests.conftest import API


@pytest.fixture
def account(create_account):
    return create_account("Ledger Owner")


# ---------- credit / debit rules ----------


def test_credit_increases_balance(account, post_txn, balance_of):
    post_txn(account["id"], "credit", "100")

    response = post_txn(account["id"], "credit", "50")

    assert response.status_code == 201
    assert balance_of(account["id"]) == "150.00"


def test_debit_decreases_balance(account, post_txn, balance_of):
    post_txn(account["id"], "credit", "100")

    response = post_txn(account["id"], "debit", "30")

    assert response.status_code == 201
    assert balance_of(account["id"]) == "70.00"


def test_debit_exceeding_balance_is_rejected_and_balance_unchanged(account, post_txn, balance_of):
    post_txn(account["id"], "credit", "100")

    response = post_txn(account["id"], "debit", "150")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INSUFFICIENT_BALANCE"
    assert error["details"]["available_balance"] == "100.00"
    assert error["details"]["requested_amount"] == "150.00"
    assert balance_of(account["id"]) == "100.00"


def test_rejected_debit_is_not_recorded(client, account, post_txn):
    post_txn(account["id"], "debit", "10", reference_id="too-much")

    # The reference is still free because nothing was persisted.
    response = post_txn(account["id"], "credit", "10", reference_id="too-much")
    assert response.status_code == 201


def test_debit_of_entire_balance_is_allowed(account, post_txn, balance_of):
    post_txn(account["id"], "credit", "100")

    assert post_txn(account["id"], "debit", "100").status_code == 201
    assert balance_of(account["id"]) == "0.00"


def test_brief_balance_consistency_example(account, post_txn, balance_of):
    for type_, amount in [("credit", "100"), ("credit", "50"), ("debit", "30"), ("debit", "20")]:
        assert post_txn(account["id"], type_, amount).status_code == 201

    assert balance_of(account["id"]) == "100.00"


def test_response_contains_all_fields(account, post_txn):
    response = post_txn(
        account["id"], "credit", "12.5", reference_id="INV-1", description="  Salary  "
    )

    body = response.json()
    assert body["account_id"] == account["id"]
    assert body["type"] == "credit"
    assert body["amount"] == "12.50"
    assert body["description"] == "Salary"
    assert body["reference_id"] == "INV-1"
    assert body["transfer_id"] is None
    uuid.UUID(body["id"])
    assert body["created_at"]


def test_decimal_precision_is_exact(account, post_txn, balance_of):
    for _ in range(10):
        post_txn(account["id"], "credit", "0.10")

    # 10 x 0.1 with floats would be 0.9999999999999999
    assert balance_of(account["id"]) == "1.00"


def test_credit_beyond_max_balance_is_rejected():
    # Reaching the NUMERIC(18,2) ceiling through the API would take ~10^7 requests,
    # so the rule is checked on the domain function directly.
    account = Account(id=uuid.uuid4(), name="x", balance=MAX_BALANCE - Decimal("1.00"))

    with pytest.raises(BalanceLimitExceededError):
        apply_movement(account, TransactionType.CREDIT, Decimal("2.00"))
    assert account.balance == MAX_BALANCE - Decimal("1.00")


# ---------- duplicates / idempotency ----------


def test_duplicate_reference_returns_409_with_original(account, post_txn, balance_of):
    first = post_txn(account["id"], "credit", "100", reference_id="INV-1")

    retry = post_txn(account["id"], "credit", "100", reference_id="INV-1")

    assert retry.status_code == 409
    error = retry.json()["error"]
    assert error["code"] == "DUPLICATE_TRANSACTION"
    assert error["details"]["existing_transaction"] == first.json()
    assert balance_of(account["id"]) == "100.00"


def test_reused_reference_with_different_payload_is_a_conflict(account, post_txn, balance_of):
    post_txn(account["id"], "credit", "100", reference_id="INV-1")

    response = post_txn(account["id"], "credit", "999", reference_id="INV-1")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REFERENCE_ID_CONFLICT"
    assert balance_of(account["id"]) == "100.00"


def test_duplicate_does_not_create_a_new_transaction(engine, account, post_txn):
    post_txn(account["id"], "credit", "100", reference_id="INV-1")
    post_txn(account["id"], "credit", "100", reference_id="INV-1")

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM transactions WHERE account_id = :id"), {"id": account["id"]}
        ).scalar()
    assert count == 1


def test_same_reference_allowed_on_different_accounts(create_account, post_txn, balance_of):
    a, b = create_account("A"), create_account("B")

    assert post_txn(a["id"], "credit", "10", reference_id="SHARED").status_code == 201
    assert post_txn(b["id"], "credit", "20", reference_id="SHARED").status_code == 201
    assert balance_of(a["id"]) == "10.00"
    assert balance_of(b["id"]) == "20.00"


# ---------- validation ----------


@pytest.mark.parametrize("amount", ["0", "-5", "0.00", "1.234", "abc", None, "NaN", "Infinity"])
def test_invalid_amount_is_rejected(account, post_txn, balance_of, amount):
    response = post_txn(account["id"], "credit", amount)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "amount"
    assert balance_of(account["id"]) == "0.00"


def test_amount_above_cap_is_rejected(account, post_txn):
    assert post_txn(account["id"], "credit", "1000000000.01").status_code == 400


def test_numeric_json_amounts_are_accepted(client, account):
    response = client.post(
        f"{API}/accounts/{account['id']}/transactions",
        json={"type": "credit", "amount": 25.5, "reference_id": "num"},
    )
    assert response.status_code == 201
    assert response.json()["amount"] == "25.50"


@pytest.mark.parametrize("type_", ["CREDIT", "deposit", "", None, 1])
def test_invalid_type_is_rejected(account, post_txn, type_):
    response = post_txn(account["id"], type_, "10")

    assert response.status_code == 400
    assert response.json()["error"]["details"][0]["field"] == "type"


@pytest.mark.parametrize("reference_id", ["", "   ", "x" * 101])
def test_invalid_reference_id_is_rejected(client, account, reference_id):
    response = client.post(
        f"{API}/accounts/{account['id']}/transactions",
        json={"type": "credit", "amount": "10", "reference_id": reference_id},
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"][0]["field"] == "reference_id"


def test_missing_reference_id_is_rejected(client, account):
    response = client.post(
        f"{API}/accounts/{account['id']}/transactions", json={"type": "credit", "amount": "10"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"][0]["field"] == "reference_id"


def test_unknown_fields_are_rejected(client, account):
    response = client.post(
        f"{API}/accounts/{account['id']}/transactions",
        json={"type": "credit", "amount": "10", "reference_id": "r", "balance": "1000"},
    )
    assert response.status_code == 400


def test_description_too_long_is_rejected(account, post_txn):
    assert post_txn(account["id"], "credit", "10", description="x" * 256).status_code == 400


def test_unknown_account_returns_404(post_txn):
    response = post_txn(str(uuid.uuid4()), "credit", "10")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_malformed_account_id_returns_400(post_txn):
    response = post_txn("12345", "credit", "10")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ACCOUNT_ID"


# ---------- immutability through the API ----------


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_transactions_cannot_be_modified_via_api(client, account, post_txn, method):
    txn = post_txn(account["id"], "credit", "10").json()
    base = f"{API}/accounts/{account['id']}/transactions"

    for url in (base, f"{base}/{txn['id']}"):
        response = getattr(client, method)(url)
        assert response.status_code in (404, 405)
        assert "error" in response.json()
