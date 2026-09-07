"""SQLAlchemy models for the Expense Approval service."""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ClaimStatus(str, enum.Enum):
    """Canonical lifecycle statuses. Stored as strings for portability."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class AuditAction(str, enum.Enum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    EDITED = "edited"


class ReportStatus(str, enum.Enum):
    """Life cycle of an expense report (Expensify-style).

    draft       -> created and editable; not yet submitted for approval
    outstanding -> submitted; awaiting the assigned approver's decision
    approved    -> approver accepted; cascaded to member claims
    rejected    -> approver rejected (comment required); cascaded to claims
    paid        -> reimbursed after approval (terminal)
    """

    DRAFT = "draft"
    OUTSTANDING = "outstanding"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # One person can be both an employee (submits) and an approver (decides).
    is_employee: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_approver: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    approves_categories: Mapped[list["Category"]] = relationship(
        back_populates="approver", foreign_keys="Category.approver_id"
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    # Routing target: each category is owned by one approver.
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    approver: Mapped[User | None] = relationship(
        back_populates="approves_categories", foreign_keys=[approver_id]
    )


class ExpenseClaim(Base):
    __tablename__ = "expense_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    submitter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    # The approver this claim was routed to at submit time.
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Optional grouping: the report this claim belongs to (approval unit when set).
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"), nullable=True)

    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    merchant: Mapped[str | None] = mapped_column(String(255), nullable=True)  # vendor / store
    description: Mapped[str] = mapped_column(Text, nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_details: Mapped[str] = mapped_column(Text, nullable=False)
    # Whether the company reimburses the submitter (out-of-pocket) vs. company card.
    reimbursable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default=ClaimStatus.PENDING.value, nullable=False)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Receipt attachment (optional).
    receipt_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)  # original name
    receipt_path: Mapped[str | None] = mapped_column(String(255), nullable=True)  # stored name

    # Cached AI review (advisory only). ai_status: "ok" | "unavailable".
    ai_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_flag_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_suggested_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_category_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def has_receipt(self) -> bool:
        return len(self.receipts) > 0

    @property
    def receipt_count(self) -> int:
        return len(self.receipts)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    submitter: Mapped[User] = relationship(foreign_keys=[submitter_id])
    approver: Mapped[User | None] = relationship(foreign_keys=[approver_id])
    category: Mapped[Category] = relationship()
    report: Mapped["Report | None"] = relationship(back_populates="claims", foreign_keys=[report_id])
    receipts: Mapped[list["Receipt"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", order_by="Receipt.id"
    )

    @property
    def is_pending(self) -> bool:
        return self.status == ClaimStatus.PENDING.value


class Receipt(Base):
    """One receipt file attached to a claim. A claim can have several."""

    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("expense_claims.id"), nullable=False, index=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)  # original name
    path: Mapped[str] = mapped_column(String(255), nullable=False)  # stored name on disk
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    claim: Mapped["ExpenseClaim"] = relationship(back_populates="receipts")

    @property
    def is_pdf(self) -> bool:
        return (self.filename or self.path or "").lower().endswith(".pdf")


class SavedView(Base):
    """A named, saved set of expense filters for one user (Expensify 'Saved').

    ``query`` holds the filter querystring (e.g. "status=pending&q=office"); the
    view is applied by linking to /claims?<query>.
    """

    __tablename__ = "saved_views"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class AuditLog(Base):
    """Append-only trail. Rows are only ever inserted, never updated or deleted."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("expense_claims.id"), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    actor: Mapped[User | None] = relationship(foreign_keys=[actor_id])


class Report(Base):
    """Groups several of a submitter's claims into one approval unit.

    All claims in a report share the same approver; approving/rejecting the report
    cascades to its claims. Statuses reuse the claim lifecycle values.
    """

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    submitter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ReportStatus.DRAFT.value, nullable=False)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    submitter: Mapped[User] = relationship(foreign_keys=[submitter_id])
    approver: Mapped[User | None] = relationship(foreign_keys=[approver_id])
    decided_by: Mapped[User | None] = relationship(foreign_keys=[decided_by_id])
    paid_by: Mapped[User | None] = relationship(foreign_keys=[paid_by_id])
    claims: Mapped[list["ExpenseClaim"]] = relationship(
        back_populates="report", foreign_keys="ExpenseClaim.report_id"
    )

    @property
    def is_draft(self) -> bool:
        return self.status == ReportStatus.DRAFT.value

    @property
    def is_outstanding(self) -> bool:
        return self.status == ReportStatus.OUTSTANDING.value

    @property
    def is_pending(self) -> bool:
        # Back-compat alias: an outstanding report is the one awaiting a decision.
        return self.status == ReportStatus.OUTSTANDING.value

    @property
    def is_approved(self) -> bool:
        return self.status == ReportStatus.APPROVED.value

    @property
    def is_paid(self) -> bool:
        return self.status == ReportStatus.PAID.value

    @property
    def total(self) -> float:
        return float(sum((c.amount for c in self.claims), 0))

    @property
    def currency(self) -> str:
        return self.claims[0].currency if self.claims else "USD"

    @property
    def groups(self) -> list[dict]:
        """Claims grouped by category name with per-group subtotals (Expensify 'Group by: Category')."""
        buckets: dict[str, list] = {}
        for c in self.claims:
            buckets.setdefault(c.category.name, []).append(c)
        out = []
        for name in sorted(buckets):
            items = buckets[name]
            out.append({
                "category": name,
                "claims": items,
                "subtotal": float(sum(x.amount for x in items)),
                "currency": items[0].currency if items else self.currency,
            })
        return out

    @property
    def activity(self) -> list[dict]:
        """Chronological report-level events for the activity feed."""
        events = [{"actor": self.submitter, "action": "created", "at": self.created_at, "comment": None}]
        if self.submitted_at:
            events.append({"actor": self.submitter, "action": "submitted", "at": self.submitted_at, "comment": None})
        if self.decided_at and self.status in (ReportStatus.APPROVED.value, ReportStatus.REJECTED.value):
            events.append({"actor": self.decided_by, "action": self.status, "at": self.decided_at, "comment": self.decision_comment})
        if self.paid_at:
            events.append({"actor": self.paid_by, "action": "paid", "at": self.paid_at, "comment": None})
        return events
