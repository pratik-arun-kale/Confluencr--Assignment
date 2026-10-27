import uuid
from datetime import timedelta

import pytest

from tests.conftest import API, parse_ts


def test_create_account_starts_with_zero_balance(client):
    response = client.post(f"{API}/accounts", json={"name": "Pratik"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Pratik"
    assert body["balance"] == "0.00"
    uuid.UUID(body["id"])
    assert parse_ts(body["created_at"]).utcoffset() == timedelta(0)


def test_account_ids_are_unique(create_account):
    ids = {create_account(f"user {i}")["id"] for i in range(5)}
    assert len(ids) == 5


def test_name_is_trimmed(client):
    response = client.post(f"{API}/accounts", json={"name": "  Pratik  "})
    assert response.json()["name"] == "Pratik"


@pytest.mark.parametrize(
    "payload", [{}, {"name": ""}, {"name": "   "}, {"name": None}, {"name": 123}]
)
def test_name_is_required(client, payload):
    response = client.post(f"{API}/accounts", json=payload)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "name"


def test_name_too_long_is_rejected(client):
    response = client.post(f"{API}/accounts", json={"name": "x" * 101})
    assert response.status_code == 400


@pytest.mark.parametrize("extra", [{"balance": 500}, {"balance": 0}, {"id": str(uuid.uuid4())}])
def test_client_cannot_set_server_fields(client, extra):
    response = client.post(f"{API}/accounts", json={"name": "Pratik", **extra})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_client_cannot_set_created_at(client):
    response = client.post(
        f"{API}/accounts", json={"name": "Pratik", "created_at": "2000-01-01T00:00:00Z"}
    )
    assert response.status_code == 400


def test_get_account(client, create_account):
    account = create_account("Pratik")

    response = client.get(f"{API}/accounts/{account['id']}")

    assert response.status_code == 200
    assert response.json() == account


def test_get_balance(client, create_account):
    account = create_account()

    response = client.get(f"{API}/accounts/{account['id']}/balance")

    assert response.status_code == 200
    assert response.json() == {"account_id": account["id"], "balance": "0.00"}


@pytest.mark.parametrize("suffix", ["", "/balance"])
def test_unknown_account_returns_404(client, suffix):
    missing = uuid.uuid4()

    response = client.get(f"{API}/accounts/{missing}{suffix}")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "ACCOUNT_NOT_FOUND"
    assert error["details"] == {"account_id": str(missing)}


@pytest.mark.parametrize("suffix", ["", "/balance"])
def test_malformed_account_id_returns_400(client, suffix):
    response = client.get(f"{API}/accounts/not-a-uuid{suffix}")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ACCOUNT_ID"
