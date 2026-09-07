"""Approver-facing routes: queue, claim detail, AI panel, approve/reject.

Claim detail is available to the submitter OR the assigned approver (scoping via
``can_view``); the AI panel and decision actions are approver-only.
"""
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.ai.service import generate_review, receipt_media_type
from app.config import settings
from app.database import get_db
from app.dependencies import require_approver, require_user
from app.export import claims_to_csv
from app.models import AuditLog, Category, ClaimStatus, ExpenseClaim, Report, ReportStatus, User
from app.services import ClaimError, approve_claim, can_view, list_saved_views, reject_claim
from app.templating import templates

router = APIRouter()


def _queue(db: Session, user: User) -> list[ExpenseClaim]:
    return (
        db.query(ExpenseClaim)
        .filter(
            ExpenseClaim.approver_id == user.id,
            ExpenseClaim.status == ClaimStatus.PENDING.value,
            ExpenseClaim.report_id.is_(None),  # reported claims are decided via the report
        )
        .order_by(ExpenseClaim.created_at.asc())
        .all()
    )


def _pending_reports(db: Session, user: User) -> list[Report]:
    # Reports are not routed to one approver: every approver sees all submitted ones.
    return (
        db.query(Report)
        .filter(Report.status == ReportStatus.OUTSTANDING.value)
        .order_by(Report.created_at.asc())
        .all()
    )


def _decided_by(db: Session, user: User) -> list[ExpenseClaim]:
    """Claims this approver has already approved or rejected."""
    return (
        db.query(ExpenseClaim)
        .filter(
            ExpenseClaim.decided_by_id == user.id,
            ExpenseClaim.status.in_(
                [ClaimStatus.APPROVED.value, ClaimStatus.REJECTED.value]
            ),
        )
        .order_by(ExpenseClaim.decided_at.desc())
        .limit(25)
        .all()
    )


@router.get("/queue")
def queue(request: Request, db: Session = Depends(get_db), user: User = Depends(require_approver)):
    return templates.TemplateResponse(
        request,
        "queue.html",
        {
            "user": user,
            "claims": _queue(db, user),
            "decided": _decided_by(db, user),
            "reports": _pending_reports(db, user),
            "saved_views": list_saved_views(db, user),
        },
    )


@router.get("/queue/table")
def queue_table(request: Request, db: Session = Depends(get_db), user: User = Depends(require_approver)):
    return templates.TemplateResponse(
        request, "_queue_table.html", {"user": user, "claims": _queue(db, user)}
    )


def _detail_context(db: Session, user: User, claim_id: int, request: Request) -> dict:
    """Shared context for the full claim page and the slide-out drawer panel.

    Raises 404/403 as appropriate. ``can_decide`` gates the approver actions.
    """
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    if not can_view(claim, user):
        raise HTTPException(status_code=403, detail="You cannot view this claim")

    audit = (
        db.query(AuditLog)
        .filter(AuditLog.claim_id == claim_id)
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        .all()
    )
    # A claim inside a report is decided via the report, not individually.
    can_decide = (
        user.is_approver
        and claim.approver_id == user.id
        and claim.is_pending
        and claim.report_id is None
    )
    return {
        "user": user,
        "claim": claim,
        "audit": audit,
        "can_decide": can_decide,
        "currencies": ["USD", "EUR", "GBP", "PLN", "UAH"],
        "error": request.query_params.get("error"),
        "msg": request.query_params.get("msg"),
    }


@router.get("/claims/{claim_id}")
def claim_detail(
    claim_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    return templates.TemplateResponse(
        request, "detail.html", _detail_context(db, user, claim_id, request)
    )


@router.get("/claims/{claim_id}/panel")
def claim_panel(
    claim_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """HTMX fragment: the claim detail rendered for the slide-out drawer."""
    return templates.TemplateResponse(
        request, "_claim_detail.html", _detail_context(db, user, claim_id, request)
    )


@router.get("/claims/{claim_id}/ai")
def claim_ai(
    claim_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_approver),
):
    """Lazy-loaded AI panel. Cached on the claim once a successful review exists."""
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim.approver_id != user.id:
        raise HTTPException(status_code=403, detail="This claim is not routed to you")

    if claim.ai_generated_at and claim.ai_status == "ok":
        review = {
            "status": claim.ai_status,
            "summary": claim.ai_summary,
            "flag": claim.ai_flag,
            "flag_reason": claim.ai_flag_reason,
            "suggested_category": claim.ai_suggested_category,
            "category_reason": claim.ai_category_reason,
        }
    else:
        categories = [c.name for c in db.query(Category).order_by(Category.name).all()]

        receipt_bytes = None
        receipt_mt = None
        if claim.receipts:
            r0 = claim.receipts[0]
            rpath = os.path.join(settings.upload_dir, r0.path)
            mt = receipt_media_type(r0.filename or r0.path)
            if mt and os.path.exists(rpath):
                with open(rpath, "rb") as fh:
                    receipt_bytes = fh.read()
                receipt_mt = mt

        review = generate_review(
            amount=claim.amount,
            currency=claim.currency,
            category=claim.category.name,
            description=claim.description,
            expense_date=claim.expense_date,
            merchant=claim.merchant,
            categories=categories,
            receipt_bytes=receipt_bytes,
            receipt_media_type=receipt_mt,
        )
        if review["status"] == "ok":
            claim.ai_status = review["status"]
            claim.ai_summary = review["summary"]
            claim.ai_flag = review["flag"]
            claim.ai_flag_reason = review["flag_reason"]
            claim.ai_suggested_category = review["suggested_category"]
            claim.ai_category_reason = review["category_reason"]
            claim.ai_generated_at = datetime.utcnow()
            db.commit()

    # A simple, deterministic policy violation (like Expensify's "cash, no receipt").
    review["no_receipt"] = not claim.has_receipt
    review["current_category"] = claim.category.name

    return templates.TemplateResponse(
        request, "_ai_panel.html", {"claim": claim, "review": review}
    )


@router.get("/export/approvals.csv")
def export_approvals(db: Session = Depends(get_db), user: User = Depends(require_approver)):
    claims = _queue(db, user) + _decided_by(db, user)
    return Response(
        content=claims_to_csv(claims),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="approvals.csv"'},
    )


@router.post("/queue/bulk-approve")
def bulk_approve(
    claim_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_approver),
):
    for cid in claim_ids:
        claim = db.get(ExpenseClaim, cid)
        if claim and claim.approver_id == user.id and claim.is_pending:
            try:
                approve_claim(db, claim, user)
            except ClaimError:
                pass
    return RedirectResponse("/queue", status_code=303)


@router.post("/queue/bulk-export")
def bulk_export_queue(
    claim_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_approver),
):
    ids = set(claim_ids)
    pool = _queue(db, user) + _decided_by(db, user)
    claims = [c for c in pool if c.id in ids]
    return Response(
        content=claims_to_csv(claims),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="selected-approvals.csv"'},
    )


@router.post("/claims/{claim_id}/approve")
def approve(
    claim_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_approver),
):
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    try:
        approve_claim(db, claim, user)
    except ClaimError as exc:
        return RedirectResponse(f"/claims/{claim_id}?error={exc}", status_code=303)
    return RedirectResponse("/queue", status_code=303)


@router.post("/claims/{claim_id}/reject")
def reject(
    claim_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_approver),
    comment: str = Form(""),
):
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    try:
        reject_claim(db, claim, user, comment)
    except ClaimError as exc:
        return RedirectResponse(f"/claims/{claim_id}?error={exc}", status_code=303)
    return RedirectResponse("/queue", status_code=303)
