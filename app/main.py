from fastapi import FastAPI

from app.config import get_settings
from app.errors import register_exception_handlers
from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware
from app.routers import accounts, health, transactions, transfers

DESCRIPTION = """
A transaction ledger: create accounts, post credits/debits, read balances and
paginated history, and transfer money between accounts.

**Guarantees**
* Balance changes and ledger entries are written atomically (one DB transaction).
* Per-account row locks make concurrent debits safe (no overdraft, no lost update).
* `reference_id` is idempotent per account: a repeat never creates a second entry.
* Ledger entries are immutable (no update/delete API; enforced by a DB trigger).

All errors share one envelope: `{"error": {"code", "message", "details", "request_id"}}`.
"""


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version="1.0.0", description=DESCRIPTION)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(accounts.router, prefix=settings.api_prefix)
    app.include_router(transactions.router, prefix=settings.api_prefix)
    app.include_router(transfers.router, prefix=settings.api_prefix)
    return app


app = create_app()
