"""Reconcile the ledger: recompute every balance from its transactions and check transfers.

Usage: python -m scripts.verify_ledger      (exit code 1 if any inconsistency is found)
"""

import sys

from sqlalchemy import text

from app.database import engine

BALANCE_MISMATCHES = text(
    """
    SELECT a.id, a.name, a.balance,
           COALESCE(SUM(CASE t.type WHEN 'credit' THEN t.amount ELSE -t.amount END), 0) AS ledger
    FROM accounts a
    LEFT JOIN transactions t ON t.account_id = a.id
    GROUP BY a.id
    HAVING a.balance <> COALESCE(SUM(CASE t.type WHEN 'credit' THEN t.amount ELSE -t.amount END), 0)
    """
)

# Every transfer must have exactly one debit on the source and one credit on the
# destination, each for the transfer amount.
BROKEN_TRANSFERS = text(
    """
    SELECT tr.id
    FROM transfers tr
    LEFT JOIN transactions t ON t.transfer_id = tr.id
    GROUP BY tr.id
    HAVING count(*) <> 2
        OR count(*) FILTER (WHERE t.type = 'debit' AND t.account_id = tr.from_account_id
                                  AND t.amount = tr.amount) <> 1
        OR count(*) FILTER (WHERE t.type = 'credit' AND t.account_id = tr.to_account_id
                                  AND t.amount = tr.amount) <> 1
    """
)

SUMMARY = text(
    "SELECT (SELECT count(*) FROM accounts), (SELECT count(*) FROM transactions),"
    " (SELECT count(*) FROM transfers)"
)


def main() -> int:
    # REPEATABLE READ: all checks see one consistent snapshot of a live database.
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        accounts, transactions, transfers = conn.execute(SUMMARY).one()
        mismatches = conn.execute(BALANCE_MISMATCHES).all()
        broken = conn.execute(BROKEN_TRANSFERS).scalars().all()

    print(f"Checked {accounts} accounts, {transactions} transactions, {transfers} transfers")
    for account_id, name, balance, ledger in mismatches:
        print(f"  BALANCE MISMATCH {account_id} ({name}): stored {balance}, ledger {ledger}")
    for transfer_id in broken:
        print(f"  BROKEN TRANSFER {transfer_id}: legs missing or inconsistent")

    if mismatches or broken:
        print("Ledger is INCONSISTENT")
        return 1
    print("Ledger is consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
