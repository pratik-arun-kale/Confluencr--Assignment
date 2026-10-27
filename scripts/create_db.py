"""Create the application and test databases if they do not exist.

Usage: python -m scripts.create_db
"""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import get_settings


def ensure_database(url: str) -> None:
    target = make_url(url)
    name = target.database
    if not name:
        raise SystemExit(f"No database name in URL: {target.render_as_string(hide_password=True)}")

    # Connect to the default maintenance DB; CREATE DATABASE cannot run inside a transaction.
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
        ).scalar()
        if exists:
            print(f"Database '{name}' already exists")
        else:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
            print(f"Created database '{name}'")
    admin.dispose()


def main() -> None:
    settings = get_settings()
    ensure_database(settings.database_url)
    ensure_database(settings.test_database_url)


if __name__ == "__main__":
    main()
