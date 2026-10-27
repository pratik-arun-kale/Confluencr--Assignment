# Design Notes

This document explains how the ledger stays correct and why each mechanism was chosen over
the alternatives. The core invariant is:

> For every account, `balance = Σ credits − Σ debits` over its stored transactions, and
> `balance ≥ 0`, at every committed point in time.

## 1. Architecture

```
HTTP ──► routers/   parse + validate input (Pydantic), shape output. No business logic.
           │
           ▼
         services/  business rules; owns the DB transaction boundary (`with db.begin()`)
           │
           ▼
         models/    SQLAlchemy tables + constraints  ──►  PostgreSQL (constraints, trigger)
```

- A synchronous SQLAlchemy session is opened per request (FastAPI runs sync endpoints in a
  thread pool). Async would add complexity without benefit here, because every write
  serialises on a row lock anyway.
- Each write service is a single `with db.begin():` block. Any exception inside it, whether a
  domain error such as insufficient balance or a database error, rolls back **everything**.
  This is how atomicity (section 5 of the brief) is achieved: the database transaction is the
  unit of work.

## 2. Concurrency control

**The problem.** Suppose two debits of 80 arrive at the same moment for an account holding
100. Each reads the balance as 100, each passes the check, and the balance ends at −60 or 20
(a lost update) depending on interleaving. The same race lets two requests with the same
`reference_id` both pass a "does it exist?" check.

**Options considered.**

| Approach | How it works | Why not (or why) |
|---|---|---|
| Check in application code only | Read, check, write | Broken under concurrency, as described above |
| Atomic conditional `UPDATE … SET balance = balance - x WHERE balance >= x` | Single statement | Correct for one balance, but a duplicate check and the insert still need to happen in the same critical section, and it gets awkward for two-account transfers |
| Optimistic locking (version column) | Retry on conflict | Every conflicting request fails and must be retried. On a "hot" account, retries cascade. |
| `SERIALIZABLE` isolation | DB detects anomalies | Correct, but conflicts surface as serialization failures that need a retry loop around every write |
| **Pessimistic row lock (`SELECT … FOR UPDATE`)** ✅ | Writers on an account queue up | Simple and deterministic. Requests wait briefly instead of failing. Only writers on the **same** account are serialised; different accounts proceed in parallel. |

**The chosen flow** (`services/transactions.py`):

```
BEGIN
  SELECT … FROM accounts WHERE id = :id FOR UPDATE   -- 404 if absent; others now wait here
  look up (account_id, reference_id)                 -- 409 if it exists
  check balance (debit) / limit (credit)             -- 422 if violated
  INSERT transaction; UPDATE balance
COMMIT                                               -- lock released
```

Because the lock is taken **first**, both the duplicate check and the balance check run while
no other writer can touch this account. That makes them race-free.

**Bounded waiting.** Each connection sets `lock_timeout` (default 5 s, `DB_LOCK_TIMEOUT_MS`).
A request that can't get the lock in time fails with `503 LOCK_TIMEOUT` and `Retry-After`
rather than holding a worker and a connection indefinitely.

**Evidence.** `tests/test_concurrency.py` releases 20–50 threads at once using a barrier,
each with its own DB connection:

- 20 debits of 10 on a balance of 100 → exactly 10 succeed and the balance is 0.00.
- 15 credits and 15 debits running together → no lost updates.
- 20 identical `reference_id`s → exactly 1 row is created.
- 50 opposing transfers A↔B → none deadlock and money is conserved.

## 3. Transfers and deadlock avoidance

A transfer locks **two** rows. If T1 locks A then waits for B while T2 locks B then waits for
A, the two deadlock. PostgreSQL would detect this and abort one of them, which shows up as an
error to the client.

`lock_accounts()` therefore always locks in **ascending account-id order**, using a single
`SELECT … WHERE id IN (…) ORDER BY id FOR UPDATE`. With a global lock order, a wait cycle
cannot form.

Inside the same transaction, the transfer then debits the source, credits the destination,
inserts the `transfers` row, and inserts two ordinary ledger entries (one debit and one
credit) linked by `transfer_id`. Insufficient funds, a missing account or a duplicate
reference all raise before the commit, so a partial transfer cannot be committed.

Representing each leg as a normal transaction means the invariant `balance = Σcredits −
Σdebits` holds for transfers without any special cases, and both accounts' histories show the
movement.

## 4. Idempotency (`reference_id`)

The brief requires that a repeated `reference_id` on the same account creates nothing and
changes nothing. It is enforced at two levels:

1. **In the service:** under the row lock, an existing `(account_id, reference_id)` produces a
   409 before any write.
2. **In the database:** `UNIQUE (account_id, reference_id)`. If the pre-check were ever
   bypassed, the insert fails, the transaction rolls back, and the `IntegrityError` is mapped
   to the same 409. `tests/test_safety_nets.py` disables the pre-check to prove this.

**409 responses are split into two codes:**

- `DUPLICATE_TRANSACTION`: the payload is identical, so this is almost certainly a client
  retry after a timeout. The response includes the **original transaction**, so the client
  can reconcile without another request.
- `REFERENCE_ID_CONFLICT`: the same reference arrived with a different amount, type or
  description. This is a client bug, so it gets a distinct code rather than looking like a
  successful retry.

An alternative is Stripe-style idempotency (return `201`/`200` with the original). It was not
chosen because the brief asks for a response "indicating that the transaction already exists",
and 409 states that explicitly.

## 5. Immutability

Ledger rows are append-only, enforced at three levels:

1. **API:** no update or delete endpoints exist (they return `405`/`404`).
2. **Code:** services only ever `INSERT` transactions and transfers.
3. **Database:** a `BEFORE UPDATE OR DELETE` trigger on `transactions` and `transfers` raises
   an exception, so even a manual `psql` session or a future buggy code path cannot rewrite
   history. `tests/test_integrity.py` attempts this with raw SQL.

The only mutable money column is `accounts.balance`, which is a **cached projection** of the
ledger. `scripts/verify_ledger.py` recomputes it from the ledger and reports any drift. It
also checks that every transfer has exactly one matching debit leg and one matching credit
leg. CI runs it after the tests.

## 6. Data model and constraints

| Constraint | Protects |
|---|---|
| `accounts.balance >= 0` (CHECK) | Last line of defence against overdraft bugs |
| `accounts.name` non-blank (CHECK) | Required name |
| `transactions.type IN ('credit','debit')` | Valid type |
| `transactions.amount > 0`, `transfers.amount > 0` | Positive amounts |
| `UNIQUE (account_id, reference_id)` | Idempotency per account |
| `UNIQUE (from_account_id, reference_id)` on transfers | Transfer idempotency |
| `from_account_id <> to_account_id` | No self-transfers |
| Foreign keys | No orphaned entries |
| Immutability trigger | Append-only ledger |

**Money** is `NUMERIC(18,2)` in the database and `Decimal` in Python. In JSON it is a string
(`"0.10"`), because a JSON number may be parsed as a binary float by clients
(`0.1 + 0.2 ≠ 0.3`). Inputs with more than 2 decimal places are rejected rather than silently
rounded.

**Timestamps** use `clock_timestamp()` rather than `now()`. `now()` is frozen at the start of a
database transaction, so both legs of a transfer would get identical times. The database
session time zone is UTC.

**Ordering.** "Latest first by `created_at`" needs a deterministic tie-breaker, because two
rows can share a timestamp. A `seq BIGINT GENERATED ALWAYS AS IDENTITY` column provides strict
insertion order. Composite indexes on `(account_id, created_at DESC, seq DESC)` (with and
without `type`) serve the history query directly.

**Pagination consistency.** The count and the page are read in one `REPEATABLE READ`
snapshot, so `total_records` always agrees with `data`, even while new transactions are
being posted.

## 7. Validation and errors

Validation happens in layers:

1. Pydantic validates shape and format: types, ranges, decimal places, lengths and unknown
   fields (`extra="forbid"`).
2. The service layer checks state-dependent rules: the account exists, the balance is
   sufficient, the reference is not a duplicate.
3. Database constraints guarantee the invariants.

All errors pass through one envelope (`app/errors.py`). Domain errors are typed exceptions
(`InsufficientBalanceError` and so on) that carry their HTTP status and machine-readable
`code`, so services never deal with HTTP. FastAPI's default 422 validation response is
replaced with `400 VALIDATION_ERROR` and per-field details, keeping **422 for business-rule
violations** (the request was well-formed, but the ledger state does not allow it).

Unexpected exceptions are caught in the middleware while the request ID is still known. They
are logged with a stack trace and returned as a generic `500 INTERNAL_ERROR` that never
exposes internals.

## 8. Testing strategy

| Layer | Technique |
|---|---|
| Behaviour per requirement | Example-based API tests via `TestClient` against real PostgreSQL |
| Concurrency | Barrier-synchronised threads, each with its own DB connection |
| Invariants | Hypothesis generates random sequences of credits, debits, transfers and retries. The API must match an in-memory model, and `balance = Σcredits − Σdebits ≥ 0` must hold. |
| Database guarantees | Raw SQL attempts to break each constraint and the trigger |
| Defence in depth | The pre-check is bypassed to prove the DB constraint still yields a 409 |

Tests use a separate `ledger_test` database that is migrated from scratch each run (which
also exercises the Alembic migration) and truncated between tests. Mocking the database would
hide exactly the locking and constraint behaviour that matters here.

## 9. What I would add for production at scale

These were deliberately left out of scope for this assignment:

- **Authentication and authorisation:** OAuth2/JWT, and checking that the caller owns an account.
- **Rate limiting** per client and per account.
- **Double-entry bookkeeping:** explicit journal entries with balanced postings, including
  external "world" accounts for deposits and withdrawals. This generalises the current
  transfer legs.
- **Outbox pattern:** write domain events (`TransactionPosted`) in the same database
  transaction, then publish them to a message broker for downstream consumers.
- **Cursor (keyset) pagination** on `(created_at, seq)` for deep histories. `OFFSET` gets
  slower as the page number grows.
- **Hot-account mitigation:** for accounts with very high write rates, split the balance into
  sub-balances or batch the postings.
- **Partitioning** `transactions` by time, plus read replicas for history queries.
- **Observability:** JSON logs, OpenTelemetry traces, and metrics for lock waits and
  409/422 rates.
- **Multi-currency support:** a currency column, using per-currency minor units.
