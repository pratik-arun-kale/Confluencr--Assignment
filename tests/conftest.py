"""Test setup: point the app at the test database *before* it is imported,
rebuild the schema with Alembic once per run, and truncate tables between tests."""

import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from app.config import get_settings

os.environ["DATABASE_URL"] = get_settings().test_database_url
os.environ["DB_LOCK_TIMEOUT_MS"] = "1500"  # keeps the lock-timeout test fast
get_settings.cache_clear()

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from app.database import engine as app_engine  # noqa: E402
from app.main import app  # noqa: E402

API = "/api/v1"


@pytest.fixture(scope="session")
def engine() -> Engine:
    assert app_engine.url.database == "ledger_test", "Refusing to run tests on a non-test DB"
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = os.environ["DATABASE_URL"]
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return app_engine


@pytest.fixture(autouse=True)
def clean_db(engine: Engine) -> Iterator[None]:
    yield
    # TRUNCATE is not a row-level UPDATE/DELETE, so the immutability trigger allows it.
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE transactions, transfers, accounts RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def client(engine: Engine) -> TestClient:
    return TestClient(app)


@pytest.fixture
def create_account(client: TestClient) -> Callable[..., dict[str, Any]]:
    def _create(name: str = "Test User") -> dict[str, Any]:
        response = client.post(f"{API}/accounts", json={"name": name})
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        return body

    return _create


@pytest.fixture
def post_txn(client: TestClient) -> Callable[..., Any]:
    counter = {"n": 0}

    def _post(
        account_id: str, type_: str, amount: str, reference_id: str | None = None, **extra: Any
    ) -> Any:
        counter["n"] += 1
        payload = {
            "type": type_,
            "amount": amount,
            "reference_id": reference_id or f"ref-{counter['n']}",
            **extra,
        }
        return client.post(f"{API}/accounts/{account_id}/transactions", json=payload)

    return _post


@pytest.fixture
def balance_of(client: TestClient) -> Callable[[str], str]:
    def _balance(account_id: str) -> str:
        response = client.get(f"{API}/accounts/{account_id}/balance")
        assert response.status_code == 200, response.text
        value: str = response.json()["balance"]
        return value

    return _balance
