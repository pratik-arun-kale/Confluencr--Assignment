# Transaction Ledger API

[![CI](https://github.com/pratik-arun-kale/Confluencr--Assignment/actions/workflows/ci.yml/badge.svg)](https://github.com/pratik-arun-kale/Confluencr--Assignment/actions/workflows/ci.yml)

A REST API for a transaction ledger: create accounts, post credits and debits, read balances
and paginated history, and transfer money between accounts. It is built with
**FastAPI + PostgreSQL**, and its focus is that **the balance is always correct**, including
when requests arrive concurrently, are retried, or fail halfway.

| Document | Contents |
|---|---|
| [docs/API.md](docs/API.md) | Every endpoint, with request/response examples and error codes |
| [DESIGN.md](DESIGN.md) | How consistency is guaranteed, and the trade-offs behind each decision |
| [ASSUMPTIONS.md](ASSUMPTIONS.md) | Interpretations made where the brief was open |
| `http://127.0.0.1:8000/docs` | Interactive Swagger UI (ReDoc at `/redoc`) once the app is running |

## Highlights

- **Atomic:** each ledger entry and its balance update are committed in one database transaction. A transfer's two legs are committed together, or neither is.
- **Safe under concurrency:** per-account row locks (`SELECT … FOR UPDATE`) mean concurrent debits can neither overdraw an account nor lose an update. Transfers take their locks in a fixed order, so they cannot deadlock.
- **Idempotent:** `reference_id` is unique per account. A repeated request never creates a second entry. An identical retry gets back the original transaction; reusing a reference for a different payload is reported as a conflict.
- **Immutable ledger:** there is no update or delete API, and a PostgreSQL trigger rejects `UPDATE`/`DELETE` on ledger tables even from raw SQL.
- **Defence in depth:** rules are enforced in Pydantic validation, again in the service layer, and finally by database `CHECK`/`UNIQUE`/`FK` constraints.
- **Exact money:** amounts are `NUMERIC(18,2)` in the database and `Decimal` in Python, never floats.
- **One error format:** every error returns `{"error": {code, message, details, request_id}}` with the correct HTTP status.
- **Verified:** 130 tests (98% coverage), including real concurrency races, property-based tests (Hypothesis), and raw-SQL integrity tests. CI runs lint, type-checking and tests against PostgreSQL.

## Tech stack

Python 3.10+ · FastAPI · SQLAlchemy 2.0 · PostgreSQL 13+ (developed on 17) · Alembic ·
Pydantic v2 · pytest + Hypothesis · ruff · mypy (strict)

## Running locally

### Prerequisites

- Python **3.10 or newer**
- A running **PostgreSQL** server, with a user that may create databases (e.g. `postgres`)

Docker is not required.

### 1. Install

```powershell
git clone https://github.com/pratik-arun-kale/Confluencr--Assignment.git
cd Confluencr--Assignment
python -m venv .venv
.\.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
```

### 2. Configure

```powershell
copy .env.example .env               # macOS/Linux: cp .env.example .env
```

Edit `.env` and set your PostgreSQL credentials:

```ini
DATABASE_URL=postgresql+psycopg://postgres:<password>@localhost:5432/ledger
TEST_DATABASE_URL=postgresql+psycopg://postgres:<password>@localhost:5432/ledger_test
```

> Special characters in the password must be URL-encoded. For example, `p@ss` becomes `p%40ss`.

### 3. Create the databases and apply migrations

```powershell
python -m scripts.create_db          # creates "ledger" and "ledger_test" if missing
alembic upgrade head                 # tables, constraints, immutability trigger
```

### 4. Run

```powershell
uvicorn app.main:app --reload
```

Then open **http://127.0.0.1:8000/docs**.

#### Quick smoke test

```bash
curl -X POST http://127.0.0.1:8000/api/v1/accounts -H "Content-Type: application/json" -d '{"name": "Alice"}'
# use the returned id:
curl -X POST http://127.0.0.1:8000/api/v1/accounts/<id>/transactions -H "Content-Type: application/json" \
     -d '{"type": "credit", "amount": "100.00", "reference_id": "INV-001"}'
curl http://127.0.0.1:8000/api/v1/accounts/<id>/balance
```

On Windows, `tasks.ps1` wraps the same steps: `.\tasks.ps1 setup | run | test | lint | verify`.

## Tests and quality checks

```powershell
pytest                     # 130 tests against the ledger_test database
pytest --cov               # with coverage report
ruff check . ; ruff format --check .
mypy                       # strict type-checking of app/ and scripts/
python -m scripts.verify_ledger   # recompute every balance from the ledger
```

The test suite migrates `ledger_test` from scratch at the start of each run and truncates it
between tests. It never touches the `ledger` database.

| Test file | What it proves |
|---|---|
| `test_accounts.py` | Accounts start at 0. The client cannot set a balance, id or timestamp. Name validation works. |
| `test_transactions.py` | Credit/debit arithmetic is correct. Overdrafts are rejected. Duplicates are handled. Every validation rule applies. |
| `test_history.py` | Type filtering, pagination metadata, latest-first ordering and parameter validation behave as specified. |
| `test_transfers.py` | Transfers are atomic, all failure modes roll back fully, and duplicate transfers are detected. |
| `test_concurrency.py` | Parallel debits cannot overdraw. Parallel duplicates create one entry. Opposing transfers cannot deadlock. |
| `test_properties.py` | For random sequences of operations, `balance == Σcredits − Σdebits ≥ 0` and money is conserved. |
| `test_integrity.py` | Raw SQL cannot edit or delete ledger rows, and cannot violate any constraint. |
| `test_safety_nets.py` | If the application's duplicate check were bypassed, the database constraint still returns 409. |
| `test_errors.py` | The error format, request IDs, malformed JSON, 404/405, the 500 response (which leaks no internals) and lock timeouts. |
| `test_verify_ledger.py` | The reconciliation script detects a tampered balance. |

## Project structure

```
app/
├── main.py            # app factory: middleware, routers, exception handlers
├── config.py          # settings from environment / .env
├── database.py        # engine (lock_timeout, UTC), session-per-request dependency
├── models.py          # SQLAlchemy models + constraints
├── schemas.py         # Pydantic request/response models (money handling)
├── errors.py          # domain errors + the single error envelope
├── middleware.py      # request id, access log, last-resort 500 handler
├── routers/           # HTTP layer only: accounts, transactions, transfers, health
└── services/          # business rules and transaction boundaries
migrations/            # Alembic: schema, constraints, immutability trigger
scripts/               # create_db, verify_ledger (reconciliation)
tests/                 # see table above
docs/API.md            # API reference
```

The layering is strict: **routers** parse HTTP input and shape responses, **services** hold
every business rule and own the database transaction, and **models** describe the schema.

## Requirement coverage

| Requirement in the brief | Implementation |
|---|---|
| 1. Create account (unique id, name required, balance 0, server timestamp) | `POST /api/v1/accounts`: UUID PK, DB default `0`, unknown fields rejected |
| 2. Add transaction (credit/debit rules, rejection on insufficient funds, duplicates) | `POST /api/v1/accounts/{id}/transactions`: row lock, then duplicate check, then balance check, then insert |
| 3. Get balance (404 if missing) | `GET /api/v1/accounts/{id}/balance` |
| 4. History (type filter, page/limit, metadata, latest first) | `GET /api/v1/accounts/{id}/transactions?type=&page=&limit=` |
| 5. Data integrity (immutability, balance consistency, atomicity) | No mutating endpoints, DB trigger, single-transaction writes, reconciliation script |
| 7. Validations | Pydantic schemas, service rules and DB constraints |
| 8. Error handling | One error envelope; statuses 400/404/405/409/422/500/503 |
| 9. Bonus: transfer | `POST /api/v1/transfers`: deadlock-free locking, all-or-nothing |
| 10. Technical (structure, validation, errors, constraints, README) | This repository |
