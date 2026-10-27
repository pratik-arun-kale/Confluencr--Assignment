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

Setup takes about 10 minutes on a fresh machine. Docker is **not** required; you only need
Python and PostgreSQL.

### Step 0: Install the prerequisites (skip any you already have)

**1. Python 3.10 or newer** (3.11+ recommended). Check with `python --version`.

- **Windows:** download it from [python.org](https://www.python.org/downloads/). In the installer, tick **"Add python.exe to PATH"**.
- **macOS:** `brew install python@3.12`
- **Ubuntu/Debian:** `sudo apt install python3 python3-venv python3-pip`

**2. PostgreSQL 13 or newer** (developed on 17). Make sure it is running on port `5432`.

- **Windows:** use the [EDB installer](https://www.postgresql.org/download/windows/). Keep the defaults and **write down the password you choose for the `postgres` user**. PostgreSQL then runs as a Windows service and starts automatically.
- **macOS:**
  ```bash
  brew install postgresql@17 && brew services start postgresql@17
  createuser -s postgres            # Homebrew doesn't create a "postgres" user by default
  psql -d postgres -c "ALTER USER postgres PASSWORD 'postgres';"
  ```
- **Ubuntu/Debian:**
  ```bash
  sudo apt install postgresql
  sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
  ```

The app needs a PostgreSQL user that is allowed to create databases. The built-in `postgres`
superuser works.

### Step 1: Get the code and install dependencies

**Windows (PowerShell):**

```powershell
git clone https://github.com/pratik-arun-kale/Confluencr--Assignment.git
cd Confluencr--Assignment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

> If PowerShell says *"running scripts is disabled on this system"*, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again.

**macOS / Linux:**

```bash
git clone https://github.com/pratik-arun-kale/Confluencr--Assignment.git
cd Confluencr--Assignment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Your prompt should now start with `(.venv)`. **Every later command must be run in this
activated terminal.** If you open a new terminal, `cd` into the project and run the
activate command again.

### Step 2: Tell the app how to reach PostgreSQL

Copy the example settings file:

```powershell
copy .env.example .env          # macOS/Linux: cp .env.example .env
```

Open `.env` in any text editor. Replace the second `postgres` in **both** URLs (the part
after the colon, which is the password) with your PostgreSQL password:

```ini
#                            user     password       host      port  database
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/ledger
TEST_DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/ledger_test
```

> If your password contains special characters, encode them: `@` → `%40`, `#` → `%23`,
> `:` → `%3A`, `/` → `%2F`, `%` → `%25`. For example, `Secret@1` becomes `Secret%401`.

You don't need to create any databases by hand; the next step does it.

### Step 3: Create the databases and tables

```bash
python -m scripts.create_db     # creates the "ledger" and "ledger_test" databases
alembic upgrade head            # creates tables, constraints and the immutability trigger
```

Expected output: `Created database 'ledger'`, `Created database 'ledger_test'`, followed by
an Alembic line `Running upgrade  -> 0001`. Both commands are safe to run again: a second
run reports that the databases already exist and applies nothing.

### Step 4: Start the API

```bash
uvicorn app.main:app --reload
```

When you see `Uvicorn running on http://127.0.0.1:8000`, open these in a browser:

- **http://127.0.0.1:8000/docs**: interactive Swagger UI. Click an endpoint, then **Try it out**, then **Execute**.
- **http://127.0.0.1:8000/health**: should show `{"status":"ok","database":"up"}`.

Press `Ctrl+C` to stop the server.

### Step 5 (optional): Try it from the command line

Open a **second** terminal and leave the server running in the first.

**macOS / Linux / Git Bash:**

```bash
BASE=http://127.0.0.1:8000/api/v1
ID=$(curl -s -X POST $BASE/accounts -H "Content-Type: application/json" \
     -d '{"name": "Alice"}' | python -c "import sys, json; print(json.load(sys.stdin)['id'])")

curl -s -X POST $BASE/accounts/$ID/transactions -H "Content-Type: application/json" \
     -d '{"type": "credit", "amount": "100.00", "reference_id": "INV-001"}'
curl -s -X POST $BASE/accounts/$ID/transactions -H "Content-Type: application/json" \
     -d '{"type": "debit", "amount": "30.00", "reference_id": "INV-002"}'
curl -s $BASE/accounts/$ID/balance                  # {"account_id": "...", "balance": "70.00"}
curl -s "$BASE/accounts/$ID/transactions?page=1&limit=10"
```

**Windows PowerShell:**

```powershell
$base = "http://127.0.0.1:8000/api/v1"
$acc = Invoke-RestMethod -Method Post "$base/accounts" -ContentType "application/json" -Body '{"name": "Alice"}'
$id = $acc.id

Invoke-RestMethod -Method Post "$base/accounts/$id/transactions" -ContentType "application/json" `
  -Body '{"type": "credit", "amount": "100.00", "reference_id": "INV-001"}'
Invoke-RestMethod -Method Post "$base/accounts/$id/transactions" -ContentType "application/json" `
  -Body '{"type": "debit", "amount": "30.00", "reference_id": "INV-002"}'
Invoke-RestMethod "$base/accounts/$id/balance"      # balance : 70.00
Invoke-RestMethod "$base/accounts/$id/transactions?page=1&limit=10" | ConvertTo-Json -Depth 5
```

Every endpoint, including transfers, is documented with examples in [docs/API.md](docs/API.md).

### Windows shortcut

`tasks.ps1` wraps the same steps. Run `.\tasks.ps1 setup` (run it again after editing `.env`
the first time), then `.\tasks.ps1 run`. The other tasks are `test`, `lint` and `verify`.

### Troubleshooting

| Problem | Fix |
|---|---|
| `password authentication failed for user "postgres"` | The password in `.env` is wrong, or has special characters that need encoding (see Step 2). |
| `connection refused` / `could not connect to server` | PostgreSQL isn't running. **Windows:** open *Services* and start `postgresql-x64-17`. **macOS:** `brew services start postgresql@17`. **Linux:** `sudo systemctl start postgresql`. |
| `permission denied to create database` | The user in `.env` can't create databases. Use `postgres`, or run `ALTER USER <name> CREATEDB;`. |
| `relation "accounts" does not exist` | Step 3 was skipped. Run `alembic upgrade head`. |
| `'python' / 'alembic' / 'uvicorn' is not recognized` | The virtual environment isn't active. Re-run the activate command from Step 1. On macOS/Linux, use `python3` before the venv exists. |
| `address already in use` (port 8000) | Another program is using the port. Run `uvicorn app.main:app --port 8001` instead. |

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
