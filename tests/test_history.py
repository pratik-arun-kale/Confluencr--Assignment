import uuid

import pytest

from tests.conftest import API, parse_ts


@pytest.fixture
def account(create_account):
    return create_account("History Owner")


@pytest.fixture
def history(client, account):
    def _get(**params):
        return client.get(f"{API}/accounts/{account['id']}/transactions", params=params)

    return _get


@pytest.fixture
def seeded(account, post_txn):
    """credit 100, debit 10, credit 20, debit 5, credit 30 -> refs r1..r5 in that order."""
    plan = [("credit", "100"), ("debit", "10"), ("credit", "20"), ("debit", "5"), ("credit", "30")]
    for i, (type_, amount) in enumerate(plan, start=1):
        assert post_txn(account["id"], type_, amount, reference_id=f"r{i}").status_code == 201
    return plan


def refs(response):
    return [t["reference_id"] for t in response.json()["data"]]


def test_returns_both_types_by_default(history, seeded):
    response = history()

    assert response.status_code == 200
    assert {t["type"] for t in response.json()["data"]} == {"credit", "debit"}
    assert response.json()["pagination"]["total_records"] == 5


def test_latest_first_ordering(history, seeded):
    response = history()

    assert refs(response) == ["r5", "r4", "r3", "r2", "r1"]
    timestamps = [parse_ts(t["created_at"]) for t in response.json()["data"]]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.parametrize(
    ("type_", "expected"), [("credit", ["r5", "r3", "r1"]), ("debit", ["r4", "r2"])]
)
def test_filter_by_type(history, seeded, type_, expected):
    response = history(type=type_)

    assert refs(response) == expected
    assert response.json()["pagination"]["total_records"] == len(expected)


def test_pagination_metadata_and_pages(history, seeded):
    first = history(page=1, limit=2).json()
    second = history(page=2, limit=2).json()
    third = history(page=3, limit=2).json()

    assert first["pagination"] == {"page": 1, "limit": 2, "total_records": 5, "total_pages": 3}
    assert [t["reference_id"] for t in first["data"]] == ["r5", "r4"]
    assert [t["reference_id"] for t in second["data"]] == ["r3", "r2"]
    assert [t["reference_id"] for t in third["data"]] == ["r1"]


def test_pagination_combined_with_filter(history, seeded):
    body = history(type="credit", page=2, limit=2).json()

    assert [t["reference_id"] for t in body["data"]] == ["r1"]
    assert body["pagination"] == {"page": 2, "limit": 2, "total_records": 3, "total_pages": 2}


def test_page_past_the_end_is_empty(history, seeded):
    body = history(page=10, limit=2).json()

    assert body["data"] == []
    assert body["pagination"]["total_pages"] == 3


def test_empty_account(history):
    body = history().json()

    assert body["data"] == []
    assert body["pagination"] == {"page": 1, "limit": 20, "total_records": 0, "total_pages": 0}


def test_default_page_size(history, account, post_txn):
    for i in range(25):
        post_txn(account["id"], "credit", "1", reference_id=f"bulk-{i}")

    body = history().json()

    assert len(body["data"]) == 20
    assert body["pagination"]["total_pages"] == 2


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"page": 0}, "page"),
        ({"page": -1}, "page"),
        ({"page": "abc"}, "page"),
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"limit": "ten"}, "limit"),
        ({"type": "refund"}, "type"),
    ],
)
def test_invalid_query_params_return_400(history, params, field):
    response = history(**params)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == field


def test_only_own_transactions_are_returned(client, create_account, post_txn, history, account):
    other = create_account("Other")
    post_txn(other["id"], "credit", "10", reference_id="other")
    post_txn(account["id"], "credit", "10", reference_id="mine")

    assert refs(history()) == ["mine"]


def test_unknown_account_returns_404(client):
    response = client.get(f"{API}/accounts/{uuid.uuid4()}/transactions")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_malformed_account_id_returns_400(client):
    response = client.get(f"{API}/accounts/xyz/transactions")
    assert response.json()["error"]["code"] == "INVALID_ACCOUNT_ID"
