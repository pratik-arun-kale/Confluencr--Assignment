from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str, lock_timeout_ms: int) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        # UTC timestamps in responses regardless of the server's local time zone.
        connect_args={"options": f"-c lock_timeout={lock_timeout_ms} -c timezone=UTC"},
    )


_settings = get_settings()
engine = make_engine(_settings.database_url, _settings.db_lock_timeout_ms)

# expire_on_commit=False lets routers serialize ORM objects after the service commits.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed (and rolled back if open)."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
