"""Property-based test: for ANY random sequence of credits, debits, transfers and retries,
the API must agree with a trivially-correct in-memory model, and the ledger invariants
must hold:

    * balance == sum(credits) - sum(debits)   for every account
    * balance >= 0                            for every account
    * transfers never create or destroy money
"""

from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import text

from tests.conftest import API

ACCOUNTS = 3
amounts = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("300"), places=2)
account_index = st.integers(min_value=0, max_value=ACCOUNTS - 1)

operation = st.one_of(
    st.tuples(st.just("credit"), account_index, amounts),
    st.tuples(st.just("debit"), account_index, amounts),
    st.tuples(st.just("transfer"), account_index, account_index, amounts),
    st.tuples(st.just("retry")),
)


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(ops=st.lists(operation, min_size=1, max_size=25))
def test_ledger_matches_model_for_any_operation_sequence(client, engine, ops):
    ids = [
        client.post(f"{API}/accounts", json={"name": f"prop-{i}"}).json()["id"]
        for i in range(ACCOUNTS)
    ]
    model = [Decimal("0.00")] * ACCOUNTS
    external_in = Decimal("0.00")  # money that entered via credits
    external_out = Decimal("0.00")  # money that left via debits
    last_success: tuple[str, dict] | None = None

    for n, op in enumerate(ops):
        kind = op[0]

        if kind == "retry":
            if last_success is None:
                continue
            url, payload = last_success
            response = client.post(url, json=payload)
            assert response.status_code == 409, response.text
            continue

        if kind == "transfer":
            _, src, dst, amount = op
            url = f"{API}/transfers"
            payload = {
                "from_account_id": ids[src],
                "to_account_id": ids[dst],
                "amount": str(amount),
                "reference_id": f"op-{n}",
            }
            response = client.post(url, json=payload)
            if src == dst:
                assert response.status_code == 400
            elif model[src] < amount:
                assert response.status_code == 422
            else:
                assert response.status_code == 201, response.text
                model[src] -= amount
                model[dst] += amount
                last_success = (url, payload)
            continue

        _, idx, amount = op
        url = f"{API}/accounts/{ids[idx]}/transactions"
        payload = {"type": kind, "amount": str(amount), "reference_id": f"op-{n}"}
        response = client.post(url, json=payload)
        if kind == "debit" and model[idx] < amount:
            assert response.status_code == 422
        else:
            assert response.status_code == 201, response.text
            model[idx] += amount if kind == "credit" else -amount
            if kind == "credit":
                external_in += amount
            else:
                external_out += amount
            last_success = (url, payload)

    balances = [
        Decimal(client.get(f"{API}/accounts/{i}/balance").json()["balance"]) for i in ids
    ]
    assert balances == model
    assert all(b >= 0 for b in balances)
    assert sum(balances) == external_in - external_out

    with engine.connect() as conn:
        for account_id, balance in zip(ids, balances):
            ledger_sum = conn.execute(
                text(
                    "SELECT COALESCE(SUM(CASE type WHEN 'credit' THEN amount ELSE -amount END), 0)"
                    " FROM transactions WHERE account_id = :id"
                ),
                {"id": account_id},
            ).scalar()
            assert ledger_sum == balance
