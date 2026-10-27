"""The error contract: every failure uses one envelope, carries a request id, leaks nothing."""

import uuid

import pytest
from sqlalchemy import text

from app.services import transactions as transaction_service
from tests.conftest import API


def assert_envelope(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert error["request_id"] == response.headers["X-Request-ID"]
    return error


def test_malformed_json(client, create_account):
    account = create_account()

    response = client.post(
        f"{API}/accounts/{account['id']}/transactions",
        content=b'{"type": "credit", "amount": ',
        headers={"Content-Type": "application/json"},
    )

    assert_envelope(response, 400, "INVALID_JSON")


@pytest.mark.parametrize("body", [b"[]", b'"text"', b"null"])
def test_non_object_body(client, body):
    response = client.post(
        f"{API}/accounts", content=body, headers={"Content-Type": "application/json"}
    )
    assert_envelope(response, 400, "VALIDATION_ERROR")


def test_validation_details_list_every_bad_field(client, create_account):
    account = create_account()

    response = client.post(
        f"{API}/accounts/{account['id']}/transactions", json={"type": "bonus", "amount": "-1"}
    )

    error = assert_envelope(response, 400, "VALIDATION_ERROR")
    assert {d["field"] for d in error["details"]} == {"type", "amount", "reference_id"}


def test_unknown_route(client):
    assert_envelope(client.get(f"{API}/does-not-exist"), 404, "NOT_FOUND")


def test_method_not_allowed(client):
    assert_envelope(client.delete(f"{API}/accounts"), 405, "METHOD_NOT_ALLOWED")


def test_client_request_id_is_echoed(client):
    response = client.get(f"{API}/accounts/{uuid.uuid4()}", headers={"X-Request-ID": "trace-123"})

    assert response.headers["X-Request-ID"] == "trace-123"
    assert response.json()["error"]["request_id"] == "trace-123"


def test_unsafe_request_id_is_replaced(client):
    response = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert response.headers["X-Request-ID"] != "bad id\nwith newline"
    assert len(response.headers["X-Request-ID"]) == 32


def test_unexpected_error_returns_500_without_leaking(client, create_account, monkeypatch):
    account = create_account()

    def boom(*_args, **_kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(transaction_service, "create_transaction", boom)
    response = client.post(
        f"{API}/accounts/{account['id']}/transactions",
        json={"type": "credit", "amount": "1", "reference_id": "x"},
    )

    assert_envelope(response, 500, "INTERNAL_ERROR")
    assert "secret" not in response.text


def test_lock_timeout_returns_503(engine, client, create_account):
    account = create_account()

    # Another connection holds the account's row lock for longer than lock_timeout.
    with engine.begin() as conn:
        conn.execute(
            text("SELECT 1 FROM accounts WHERE id = :id FOR UPDATE"), {"id": account["id"]}
        )
        response = client.post(
            f"{API}/accounts/{account['id']}/transactions",
            json={"type": "credit", "amount": "1", "reference_id": "blocked"},
        )

    assert_envelope(response, 503, "LOCK_TIMEOUT")
    assert response.headers["Retry-After"] == "1"
    assert client.get(f"{API}/accounts/{account['id']}/balance").json()["balance"] == "0.00"


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}
