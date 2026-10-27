import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Exact decimal money: 16 integer digits, 2 fractional. Never float.
Money = Numeric(18, 2)
MAX_BALANCE = Decimal("9999999999999999.99")  # largest value NUMERIC(18, 2) can hold

# clock_timestamp() (not now()) so rows written inside one DB transaction still get
# distinct, real insertion times.
SERVER_NOW = text("clock_timestamp()")


class TransactionType(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="ck_accounts_name_not_blank"),
        CheckConstraint("balance >= 0", name="ck_accounts_balance_non_negative"),
    )
    # Load server-generated columns via RETURNING on insert instead of a second query.
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Money, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=SERVER_NOW
    )


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transfers_amount_positive"),
        CheckConstraint(
            "from_account_id <> to_account_id", name="ck_transfers_distinct_accounts"
        ),
        UniqueConstraint("from_account_id", "reference_id", name="uq_transfers_from_reference"),
    )
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    to_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=SERVER_NOW
    )


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("type IN ('credit', 'debit')", name="ck_transactions_type"),
        CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        CheckConstraint(
            "length(btrim(reference_id)) > 0", name="ck_transactions_reference_not_blank"
        ),
        # Idempotency: a reference_id can be used once per account (but reused across accounts).
        UniqueConstraint("account_id", "reference_id", name="uq_transactions_account_reference"),
        Index(
            "ix_transactions_account_created",
            "account_id",
            text("created_at DESC"),
            text("seq DESC"),
        ),
        Index(
            "ix_transactions_account_type_created",
            "account_id",
            "type",
            text("created_at DESC"),
            text("seq DESC"),
        ),
    )
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Monotonic insertion order; tie-breaker when created_at values are equal.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False)
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transfers.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=SERVER_NOW
    )
