"""Reports: Expensify-style approval unit with a full lifecycle.

draft -> submit -> outstanding -> approve/reject (cascades to member claims) -> paid.
A report is visible to its submitter or its assigned approver.
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_user
from app.models import Report, User
from app.services import (
    ClaimError,
    add_claim_to_report,
    addable_claims_for_report,
    approve_report,
    can_view_report,
    create_report,
    delete_report,
    duplicate_report,
    eligible_claims_for_report,
    list_saved_views,
    mark_paid_report,
    reject_report,
    remove_claim_from_report,
    rename_report,
    retract_report,
    submit_report,
)
from app.templating import templates

router = APIRouter()


@router.get("/reports")
def reports_list(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    reports = (
        db.query(Report)
        .filter(Report.submitter_id == user.id)
        .order_by(Report.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request,
        "reports_list.html",
        {"user": user, "reports": reports, "saved_views": list_saved_views(db, user)},
    )


@router.get("/reports/new")
def new_report_form(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return templates.TemplateResponse(
        request, "reports/new.html", {"user": user, "claims": eligible_claims_for_report(db, user)}
    )


@router.post("/reports")
def create_report_route(
    request: Request,
    name: str = Form(""),
    claim_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    try:
        report = create_report(db, user, name, claim_ids)
    except ClaimError as exc:
        return templates.TemplateResponse(
            request,
            "reports/new.html",
            {"user": user, "claims": eligible_claims_for_report(db, user), "error": str(exc), "form_name": name},
            status_code=400,
        )
    return RedirectResponse(f"/reports/{report.id}", status_code=303)


def _banner(report: Report, user: User) -> str:
    decided_by = report.decided_by.full_name if report.decided_by else "an approver"
    if report.is_draft:
        return "Draft — add expenses, then submit for approval."
    if report.is_outstanding:
        if user.is_approver:
            return "Submitted for approval — you can approve or reject it."
        return "Submitted — waiting for an approver."
    if report.is_approved:
        if user.is_approver:
            return f"Approved by {decided_by} — mark as paid once reimbursed."
        return f"Approved by {decided_by} — awaiting reimbursement."
    if report.status == "rejected":
        return f"Rejected by {decided_by}."
    if report.is_paid:
        return "Reimbursed."
    return ""


def _report_or_404(db: Session, report_id: int) -> Report:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/reports/print")
def print_reports(request: Request, ids: str = "", db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Printable export of selected reports (opened in a new tab; auto-prints -> Save as PDF)."""
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    reports = []
    for rid in id_list:
        r = db.get(Report, rid)
        if r and can_view_report(r, user):
            reports.append(r)
    return templates.TemplateResponse(request, "reports_print.html", {"user": user, "reports": reports})


@router.get("/reports/{report_id:int}")
def report_detail(report_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    report = _report_or_404(db, report_id)
    if not can_view_report(report, user):
        raise HTTPException(status_code=403, detail="You cannot view this report")
    can_decide = user.is_approver and report.is_outstanding
    can_edit = report.submitter_id == user.id and report.is_draft
    can_pay = user.is_approver and report.is_approved
    return templates.TemplateResponse(
        request,
        "report_detail.html",
        {
            "user": user,
            "report": report,
            "can_decide": can_decide,
            "can_edit": can_edit,
            "can_pay": can_pay,
            "addable": addable_claims_for_report(db, report) if can_edit else [],
            "banner": _banner(report, user),
            "error": request.query_params.get("error"),
            "msg": request.query_params.get("msg"),
        },
    )


def _act(db, report_id, user, fn, *args, redirect=None):
    report = _report_or_404(db, report_id)
    try:
        fn(db, report, user, *args)
    except ClaimError as exc:
        return RedirectResponse(f"/reports/{report_id}?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(redirect or f"/reports/{report_id}", status_code=303)


@router.post("/reports/{report_id}/submit")
def submit_route(report_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _act(db, report_id, user, submit_report)


@router.post("/reports/{report_id}/retract")
def retract_route(report_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _act(db, report_id, user, retract_report)


@router.post("/reports/{report_id}/mark-paid")
def mark_paid_route(report_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _act(db, report_id, user, mark_paid_report)


@router.post("/reports/{report_id}/rename")
def rename_route(report_id: int, name: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _act(db, report_id, user, rename_report, name)


@router.post("/reports/{report_id}/add-claim")
def add_claim_route(report_id: int, claim_id: int = Form(...), db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _act(db, report_id, user, add_claim_to_report, claim_id)


@router.post("/reports/{report_id}/remove-claim")
def remove_claim_route(report_id: int, claim_id: int = Form(...), db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _act(db, report_id, user, remove_claim_from_report, claim_id)


@router.post("/reports/{report_id}/approve")
def approve_route(report_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _act(db, report_id, user, approve_report, redirect="/queue")


@router.post("/reports/{report_id}/reject")
def reject_route(report_id: int, comment: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _act(db, report_id, user, reject_report, comment, redirect="/queue")


@router.post("/reports/bulk-submit")
def bulk_submit(report_ids: list[int] = Form(default=[]), db: Session = Depends(get_db), user: User = Depends(require_user)):
    for rid in report_ids:
        r = db.get(Report, rid)
        if r and r.submitter_id == user.id and r.is_draft:
            try:
                submit_report(db, r, user)
            except ClaimError:
                pass
    return RedirectResponse("/reports", status_code=303)


@router.post("/reports/bulk-delete")
def bulk_delete(report_ids: list[int] = Form(default=[]), db: Session = Depends(get_db), user: User = Depends(require_user)):
    for rid in report_ids:
        r = db.get(Report, rid)
        if r and r.submitter_id == user.id and r.is_draft:
            try:
                delete_report(db, r, user)
            except ClaimError:
                pass
    return RedirectResponse("/reports", status_code=303)


@router.post("/reports/bulk-duplicate")
def bulk_duplicate(report_ids: list[int] = Form(default=[]), db: Session = Depends(get_db), user: User = Depends(require_user)):
    for rid in report_ids:
        r = db.get(Report, rid)
        if r and r.submitter_id == user.id:
            try:
                duplicate_report(db, r, user)
            except ClaimError:
                pass
    return RedirectResponse("/reports", status_code=303)


@router.post("/reports/bulk-export")
def bulk_export(request: Request, report_ids: list[int] = Form(default=[]), db: Session = Depends(get_db), user: User = Depends(require_user)):
    reports = []
    for rid in report_ids:
        r = db.get(Report, rid)
        if r and can_view_report(r, user):
            reports.append(r)
    return templates.TemplateResponse(request, "reports_print.html", {"user": user, "reports": reports})
