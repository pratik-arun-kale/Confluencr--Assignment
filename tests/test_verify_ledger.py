from sqlalchemy import text

from scripts import verify_ledger
from tests.conftest import API


def test_reconciliation_passes_then_detects_tampering(
    client, engine, create_account, post_txn, capsys
):
    a, b = create_account("A")["id"], create_account("B")["id"]
    post_txn(a, "credit", "100")
    post_txn(a, "debit", "25")
    client.post(
        f"{API}/transfers",
        json={"from_account_id": a, "to_account_id": b, "amount": "10", "reference_id": "T"},
    )

    assert verify_ledger.main() == 0
    assert "Ledger is consistent" in capsys.readouterr().out

    # Simulate a bug or manual edit that changes a balance without a ledger entry.
    with engine.begin() as conn:
        conn.execute(text("UPDATE accounts SET balance = balance + 1 WHERE id = :id"), {"id": b})

    assert verify_ledger.main() == 1
    out = capsys.readouterr().out
    assert f"BALANCE MISMATCH {b}" in out
    assert "INCONSISTENT" in out
