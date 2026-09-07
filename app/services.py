"""Claim lifecycle: the only place status transitions happen.

Every transition validates the actor and current status, then writes an
append-only audit record in the same transaction. This centralization is what
makes scoping and the audit trail trustworthy.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from urllib.parse import urlencode

from app.models import AuditAction, AuditLog, Category, ClaimStatus, ExpenseClaim, Receipt, Report, ReportStatus, SavedView, User
from app.routing import resolve_approver


class ClaimError(Exception):
    """Raised on invalid input or a forbidden/illegal transition."""


def _audit(
    db: Session,
    claim: ExpenseClaim,
    actor: User | None,
    action: AuditAction,
    from_status: str | None,
    to_status: str | None,
    comment: str | None = None,
) -> None:
    db.add(
        AuditLog(
            claim_id=claim.id,
            actor_id=actor.id if actor else None,
            action=action.value,
            from_status=from_status,
            to_status=to_status,
            comment=comment,
        )
    )


def create_claim(
    db: Session,
    *,
    submitter: User,
    category: Category,
    amount: str | float | Decimal,
    description: str,
    expense_date: date,
    payment_details: str,
    currency: str = "USD",
    merchant: str | None = None,
    reimbursable: bool = True,
    receipt_filename: str | None = None,
    receipt_path: str | None = None,
) -> ExpenseClaim:
    try:
        amount_dec = Decimal(str(amount))
    except (InvalidOperation, TypeError):
        raise ClaimError("Amount must be a number")
    if amount_dec <= 0:
        raise ClaimError("Amount must be greater than zero")
    if not description.strip():
        raise ClaimError("Description is required")
    if not payment_details.strip():
        raise ClaimError("Payment details are required")

    approver = resolve_approver(category)

    claim = ExpenseClaim(
        submitter_id=submitter.id,
        category_id=category.id,
        approver_id=approver.id if approver else None,
        amount=amount_dec,
        currency=currency,
        merchant=(merchant.strip() or None) if merchant else None,
        description=description.strip(),
        expense_date=expense_date,
        payment_details=payment_details.strip(),
        reimbursable=reimbursable,
        status=ClaimStatus.PENDING.value,
    )
    db.add(claim)
    db.flush()  # assign claim.id for the audit row
    if receipt_path:
        db.add(Receipt(claim_id=claim.id, filename=receipt_filename, path=receipt_path))
    _audit(db, claim, submitter, AuditAction.SUBMITTED, None, ClaimStatus.PENDING.value)
    db.commit()
    db.refresh(claim)
    return claim


def add_receipt(db: Session, claim: ExpenseClaim, actor: User, filename: str | None, path: str) -> Receipt:
    """Attach ANOTHER receipt file to a claim (owner + pending). Claims may hold several."""
    if claim.submitter_id != actor.id:
        raise ClaimError("You can only add receipts to your own expenses.")
    if not claim.is_pending:
        raise ClaimError("Only a pending expense can be edited.")
    receipt = Receipt(claim_id=claim.id, filename=filename, path=path)
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    return receipt


def delete_receipt(db: Session, claim: ExpenseClaim, actor: User, receipt_id: int) -> str | None:
    """Remove one receipt from a claim (owner + pending). Returns its stored path for file cleanup."""
    if claim.submitter_id != actor.id:
        raise ClaimError("You can only edit your own expenses.")
    if not claim.is_pending:
        raise ClaimError("Only a pending expense can be edited.")
    receipt = db.get(Receipt, receipt_id)
    if receipt is None or receipt.claim_id != claim.id:
        raise ClaimError("Receipt not found.")
    path = receipt.path
    db.delete(receipt)
    db.commit()
    return path


def withdraw_claim(db: Session, claim: ExpenseClaim, actor: User) -> ExpenseClaim:
    if claim.submitter_id != actor.id:
        raise ClaimError("You can only withdraw your own claims")
    if not claim.is_pending:
        raise ClaimError("Only a pending claim can be withdrawn")

    prev = claim.status
    claim.status = ClaimStatus.WITHDRAWN.value
    _audit(db, claim, actor, AuditAction.WITHDRAWN, prev, claim.status)
    db.commit()
    db.refresh(claim)
    return claim


def approve_claim(db: Session, claim: ExpenseClaim, actor: User) -> ExpenseClaim:
    _assert_can_decide(claim, actor)
    prev = claim.status
    claim.status = ClaimStatus.APPROVED.value
    claim.decided_by_id = actor.id
    claim.decided_at = datetime.utcnow()
    _audit(db, claim, actor, AuditAction.APPROVED, prev, claim.status)
    db.commit()
    db.refresh(claim)
    return claim


def reject_claim(db: Session, claim: ExpenseClaim, actor: User, comment: str) -> ExpenseClaim:
    _assert_can_decide(claim, actor)
    if not comment or not comment.strip():
        raise ClaimError("A comment is required to reject a claim")
    prev = claim.status
    claim.status = ClaimStatus.REJECTED.value
    claim.decision_comment = comment.strip()
    claim.decided_by_id = actor.id
    claim.decided_at = datetime.utcnow()
    _audit(db, claim, actor, AuditAction.REJECTED, prev, claim.status, comment=comment.strip())
    db.commit()
    db.refresh(claim)
    return claim


def update_claim_fields(
    db: Session, claim: ExpenseClaim, actor: User, changes: dict, via: str = "AI assistant"
) -> dict:
    """Apply AI-assistant edits to a claim. Returns {"applied": [...], "error": str|None}.

    Only the submitter may edit, and only while the claim is pending. Changing the
    category re-routes the claim to that category's approver. Every edit is audited.
    """
    if claim.submitter_id != actor.id:
        return {"applied": [], "error": "Only the person who submitted this claim can edit it."}
    if not claim.is_pending:
        return {"applied": [], "error": "Only a pending claim can be edited."}

    applied: list[str] = []

    if changes.get("category"):
        name = str(changes["category"]).strip()
        cat = next(
            (c for c in db.query(Category).all() if c.name.lower() == name.lower()), None
        )
        if cat is None:
            available = ", ".join(c.name for c in db.query(Category).order_by(Category.name).all())
            return {"applied": [], "error": f"Unknown category '{name}'. Available: {available}."}
        claim.category_id = cat.id
        approver = resolve_approver(cat)
        claim.approver_id = approver.id if approver else None
        applied.append(f"category → {cat.name}")

    if changes.get("amount") is not None:
        try:
            amount = Decimal(str(changes["amount"]))
        except (InvalidOperation, TypeError):
            return {"applied": [], "error": "Amount must be a number."}
        if amount <= 0:
            return {"applied": [], "error": "Amount must be greater than zero."}
        claim.amount = amount
        applied.append(f"amount → {amount}")

    if changes.get("merchant") is not None and str(changes["merchant"]).strip():
        claim.merchant = str(changes["merchant"]).strip()
        applied.append(f"merchant → {claim.merchant}")

    if changes.get("reimbursable") is not None:
        claim.reimbursable = bool(changes["reimbursable"])
        applied.append(f"reimbursable → {'yes' if claim.reimbursable else 'no'}")

    if changes.get("currency"):
        cur = str(changes["currency"]).strip().upper()
        if cur in {"USD", "EUR", "GBP", "PLN", "UAH"}:
            claim.currency = cur
            applied.append(f"currency → {cur}")

    if changes.get("description") and str(changes["description"]).strip():
        claim.description = str(changes["description"]).strip()
        applied.append("description updated")

    if changes.get("expense_date"):
        try:
            claim.expense_date = date.fromisoformat(str(changes["expense_date"]))
        except ValueError:
            return {"applied": [], "error": "Expense date must be a valid date (YYYY-MM-DD)."}
        applied.append(f"date → {claim.expense_date}")

    if changes.get("payment_details") and str(changes["payment_details"]).strip():
        claim.payment_details = str(changes["payment_details"]).strip()
        applied.append("payment details updated")

    if not applied:
        return {"applied": [], "error": "Nothing to change."}

    _audit(
        db, claim, actor, AuditAction.EDITED, claim.status, claim.status,
        comment=f"Edited via {via}: " + "; ".join(applied),
    )
    db.commit()
    db.refresh(claim)
    return {"applied": applied, "error": None}


def _assert_can_decide(claim: ExpenseClaim, actor: User) -> None:
    if claim.approver_id != actor.id:
        raise ClaimError("This claim is not routed to you")
    if not claim.is_pending:
        raise ClaimError("This claim has already been decided")


def can_view(claim: ExpenseClaim, user: User) -> bool:
    """A claim is visible only to its submitter or its assigned approver."""
    return user.id in (claim.submitter_id, claim.approver_id)


# --------------------------------------------------------------------------- #
# Reports: group several claims into one approval unit (cascades to claims).   #
# --------------------------------------------------------------------------- #

def eligible_claims_for_report(db: Session, submitter: User) -> list[ExpenseClaim]:
    """Pending, not-yet-reported claims the submitter can bundle into a report."""
    return (
        db.query(ExpenseClaim)
        .filter(
            ExpenseClaim.submitter_id == submitter.id,
            ExpenseClaim.status == ClaimStatus.PENDING.value,
            ExpenseClaim.report_id.is_(None),
        )
        .order_by(ExpenseClaim.created_at.desc())
        .all()
    )


def create_report(db: Session, submitter: User, name: str, claim_ids: list[int]) -> Report:
    claims = [c for c in (db.get(ExpenseClaim, cid) for cid in claim_ids) if c is not None]
    if not claims:
        raise ClaimError("Select at least one claim.")
    for c in claims:
        if c.submitter_id != submitter.id:
            raise ClaimError("You can only report your own claims.")
        if not c.is_pending:
            raise ClaimError("Only pending claims can be added to a report.")
        if c.report_id:
            raise ClaimError("A selected claim is already in a report.")

    # A report is not routed to a specific approver: once submitted it goes into the
    # shared approval queue and ANY approver can decide it. So expenses of any
    # category (any per-claim routing) can be bundled together.
    report = Report(
        name=(name.strip() or f"Expense Report {date.today().isoformat()}"),
        submitter_id=submitter.id,
        approver_id=None,
        status=ReportStatus.DRAFT.value,
    )
    db.add(report)
    db.flush()
    for c in claims:
        c.report_id = report.id
    db.commit()
    db.refresh(report)
    return report


# --------------------------------------------------------------------------- #
# Saved views: named sets of expense filters (Expensify 'Saved').             #
# --------------------------------------------------------------------------- #

_VIEW_KEYS = ["status", "category_id", "date_from", "date_to", "amount_min", "amount_max", "q", "sort", "dir"]


def list_saved_views(db: Session, user: User) -> list[SavedView]:
    return (
        db.query(SavedView)
        .filter(SavedView.user_id == user.id)
        .order_by(SavedView.created_at.desc())
        .all()
    )


def create_saved_view(db: Session, user: User, name: str, params: dict) -> SavedView:
    name = (name or "").strip()
    if not name:
        raise ClaimError("View name is required.")
    pairs = [
        (k, str(params.get(k)).strip())
        for k in _VIEW_KEYS
        if params.get(k) not in (None, "")
    ]
    qs = urlencode([(k, v) for k, v in pairs if v])
    view = SavedView(user_id=user.id, name=name[:120], query=qs)
    db.add(view)
    db.commit()
    db.refresh(view)
    return view


def delete_saved_view(db: Session, user: User, view_id: int) -> None:
    view = db.get(SavedView, view_id)
    if view and view.user_id == user.id:
        db.delete(view)
        db.commit()


def can_view_report(report: Report, user: User) -> bool:
    # The submitter always sees their own report; any approver sees it once it is
    # submitted (drafts stay private to the submitter).
    if user.id == report.submitter_id:
        return True
    return user.is_approver and not report.is_draft


def submit_report(db: Session, report: Report, actor: User) -> Report:
    if report.submitter_id != actor.id:
        raise ClaimError("You can only submit your own reports.")
    if not report.is_draft:
        raise ClaimError("Only a draft report can be submitted.")
    if not report.claims:
        raise ClaimError("Add at least one expense before submitting.")
    report.status = ReportStatus.OUTSTANDING.value
    report.submitted_at = datetime.utcnow()
    db.commit()
    db.refresh(report)
    return report


def retract_report(db: Session, report: Report, actor: User) -> Report:
    if report.submitter_id != actor.id:
        raise ClaimError("You can only retract your own reports.")
    if not report.is_outstanding:
        raise ClaimError("Only a submitted report can be retracted.")
    report.status = ReportStatus.DRAFT.value
    report.submitted_at = None
    db.commit()
    db.refresh(report)
    return report


def approve_report(db: Session, report: Report, actor: User) -> Report:
    # Any approver can decide a submitted report — it is not routed to one person.
    if not actor.is_approver:
        raise ClaimError("Only an approver can approve reports.")
    if not report.is_outstanding:
        raise ClaimError("Only a submitted report can be approved.")
    for c in list(report.claims):
        if c.is_pending:
            # Report-level decision overrides each claim's per-category routing.
            prev = c.status
            c.status = ClaimStatus.APPROVED.value
            c.decided_by_id = actor.id
            c.decided_at = datetime.utcnow()
            _audit(db, c, actor, AuditAction.APPROVED, prev, c.status)
    report.status = ReportStatus.APPROVED.value
    report.approver_id = actor.id  # record who approved it
    report.decided_by_id = actor.id
    report.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(report)
    return report


def reject_report(db: Session, report: Report, actor: User, comment: str) -> Report:
    if not actor.is_approver:
        raise ClaimError("Only an approver can reject reports.")
    if not report.is_outstanding:
        raise ClaimError("Only a submitted report can be rejected.")
    if not comment or not comment.strip():
        raise ClaimError("A comment is required to reject a report.")
    for c in list(report.claims):
        if c.is_pending:
            prev = c.status
            c.status = ClaimStatus.REJECTED.value
            c.decision_comment = comment.strip()
            c.decided_by_id = actor.id
            c.decided_at = datetime.utcnow()
            _audit(db, c, actor, AuditAction.REJECTED, prev, c.status, comment=comment.strip())
    report.status = ReportStatus.REJECTED.value
    report.approver_id = actor.id
    report.decision_comment = comment.strip()
    report.decided_by_id = actor.id
    report.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(report)
    return report


def mark_paid_report(db: Session, report: Report, actor: User) -> Report:
    if not actor.is_approver:
        raise ClaimError("Only an approver can mark a report paid.")
    if not report.is_approved:
        raise ClaimError("Only an approved report can be marked as paid.")
    report.status = ReportStatus.PAID.value
    report.paid_by_id = actor.id
    report.paid_at = datetime.utcnow()
    db.commit()
    db.refresh(report)
    return report


def rename_report(db: Session, report: Report, actor: User, name: str) -> Report:
    if report.submitter_id != actor.id:
        raise ClaimError("You can only rename your own reports.")
    if report.status not in (ReportStatus.DRAFT.value, ReportStatus.OUTSTANDING.value):
        raise ClaimError("This report can no longer be renamed.")
    name = (name or "").strip()
    if not name:
        raise ClaimError("Report name cannot be empty.")
    report.name = name[:255]
    db.commit()
    db.refresh(report)
    return report


def addable_claims_for_report(db: Session, report: Report) -> list[ExpenseClaim]:
    """Any free pending claim of the report's submitter (category no longer matters)."""
    return eligible_claims_for_report(db, report.submitter)


def add_claim_to_report(db: Session, report: Report, actor: User, claim_id: int) -> Report:
    if report.submitter_id != actor.id:
        raise ClaimError("You can only edit your own reports.")
    if not report.is_draft:
        raise ClaimError("Expenses can only be added to a draft report.")
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None:
        raise ClaimError("Expense not found.")
    if claim.submitter_id != actor.id:
        raise ClaimError("You can only add your own expenses.")
    if not claim.is_pending or claim.report_id:
        raise ClaimError("Only a free pending expense can be added.")
    claim.report_id = report.id
    db.commit()
    db.refresh(report)
    return report


def remove_claim_from_report(db: Session, report: Report, actor: User, claim_id: int) -> Report:
    if report.submitter_id != actor.id:
        raise ClaimError("You can only edit your own reports.")
    if not report.is_draft:
        raise ClaimError("Expenses can only be removed from a draft report.")
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None or claim.report_id != report.id:
        raise ClaimError("Expense is not in this report.")
    claim.report_id = None
    db.commit()
    db.refresh(report)
    return report


def delete_report(db: Session, report: Report, actor: User) -> None:
    """Delete a DRAFT report. Its expenses are unlinked (returned to the free pool), not deleted."""
    if report.submitter_id != actor.id:
        raise ClaimError("You can only delete your own reports.")
    if not report.is_draft:
        raise ClaimError("Only a draft report can be deleted.")
    for c in list(report.claims):
        c.report_id = None
    db.delete(report)
    db.commit()


def duplicate_report(db: Session, report: Report, actor: User) -> Report:
    """Copy a report into a new DRAFT owned by the actor, cloning each expense as a fresh pending claim."""
    if report.submitter_id != actor.id:
        raise ClaimError("You can only duplicate your own reports.")
    new = Report(
        name=(report.name + " (copy)")[:255],
        submitter_id=actor.id,
        approver_id=None,
        status=ReportStatus.DRAFT.value,
    )
    db.add(new)
    db.flush()
    for c in report.claims:
        approver = resolve_approver(c.category)
        nc = ExpenseClaim(
            submitter_id=actor.id,
            category_id=c.category_id,
            approver_id=approver.id if approver else None,
            report_id=new.id,
            amount=c.amount,
            currency=c.currency,
            merchant=c.merchant,
            description=c.description,
            expense_date=c.expense_date,
            payment_details=c.payment_details,
            reimbursable=c.reimbursable,
            status=ClaimStatus.PENDING.value,
        )
        db.add(nc)
        db.flush()
        _audit(db, nc, actor, AuditAction.SUBMITTED, None, ClaimStatus.PENDING.value)
    db.commit()
    db.refresh(new)
    return new
