"""Employee-facing routes: dashboard, submit a claim, withdraw a claim."""
import os
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.service import extract_receipt_fields, generate_review, receipt_media_type
from app.config import settings
from app.database import get_db
from app.dependencies import require_user
from app.export import claims_to_csv
from app.models import Category, ClaimStatus, ExpenseClaim, Receipt, Report, User
from app.services import (
    ClaimError,
    add_receipt,
    can_view,
    create_claim,
    create_saved_view,
    delete_receipt,
    delete_saved_view,
    list_saved_views,
    update_claim_fields,
    withdraw_claim,
)
from app.templating import templates

router = APIRouter()

# Currencies a claim can be submitted in (first is the default).
CURRENCIES = ["USD", "EUR", "GBP", "PLN", "UAH"]


def _save_receipt(file: UploadFile | None) -> tuple[str | None, str | None]:
    """Persist an uploaded receipt; returns (original_name, stored_name)."""
    if not file or not file.filename:
        return None, None
    data = file.file.read()
    if not data:
        return None, None
    if len(data) > settings.max_receipt_mb * 1024 * 1024:
        raise ClaimError(f"Receipt is too large (max {settings.max_receipt_mb} MB)")
    ext = os.path.splitext(file.filename)[1][:10]
    stored = f"{uuid.uuid4().hex}{ext}"
    os.makedirs(settings.upload_dir, exist_ok=True)
    with open(os.path.join(settings.upload_dir, stored), "wb") as fh:
        fh.write(data)
    return file.filename, stored


def _read_receipt(claim: ExpenseClaim) -> tuple[bytes | None, str | None]:
    """Bytes of the claim's first receipt (for AI vision). Claims may hold several."""
    if not claim.receipts:
        return None, None
    r = claim.receipts[0]
    path = os.path.join(settings.upload_dir, r.path)
    mt = receipt_media_type(r.filename or r.path)
    if not (mt and os.path.exists(path)):
        return None, None
    with open(path, "rb") as fh:
        return fh.read(), mt


def _auto_categorize(db: Session, claim: ExpenseClaim, actor: User) -> str | None:
    """If AI confidently flags a category mismatch, change it (and re-route).

    Returns a user-facing notice when it changed the category, else None. Runs only
    for a pending claim by its submitter; degrades silently when AI is unavailable.
    """
    if not (settings.ai_auto_categorize and settings.anthropic_api_key):
        return None

    receipt_bytes, receipt_mt = _read_receipt(claim)
    categories = [c.name for c in db.query(Category).order_by(Category.name).all()]
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
    suggested = review.get("suggested_category")
    if (
        review.get("status") == "ok"
        and review.get("flag")
        and suggested
        and suggested.lower() != claim.category.name.lower()
    ):
        result = update_claim_fields(
            db, claim, actor, {"category": suggested}, via="AI auto-categorization"
        )
        if not result.get("error"):
            source = "your claim and receipt" if receipt_bytes else "your claim"
            return f"AI changed the category to {suggested} based on {source}."
    return None


def _my_claims(db: Session, user: User) -> list[ExpenseClaim]:
    return (
        db.query(ExpenseClaim)
        .filter(ExpenseClaim.submitter_id == user.id)
        .order_by(ExpenseClaim.created_at.desc())
        .all()
    )


def _status_counts(claims: list[ExpenseClaim]) -> dict[str, int]:
    counts = {s.value: 0 for s in ClaimStatus}
    for c in claims:
        counts[c.status] = counts.get(c.status, 0) + 1
    return counts


def _greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def _pending_queue_count(db: Session, user: User) -> int:
    if not user.is_approver:
        return 0
    # Match the approver's queue exactly: reported claims are decided via their
    # report, not shown as individual queue items, so they must not be counted.
    return (
        db.query(func.count(ExpenseClaim.id))
        .filter(
            ExpenseClaim.approver_id == user.id,
            ExpenseClaim.status == ClaimStatus.PENDING.value,
            ExpenseClaim.report_id.is_(None),
        )
        .scalar()
    )


@router.get("/")
def home(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Expensify-style overview: greeting, quick actions, status stats, recent."""
    claims = _my_claims(db, user)
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": user,
            "greeting": _greeting(),
            "today": date.today().strftime("%A, %B %d"),
            "counts": _status_counts(claims),
            "total": len(claims),
            "recent": claims[:5],
            "pending_queue": _pending_queue_count(db, user),
        },
    )


def _valid_status(status: str | None) -> str | None:
    return status if status in {s.value for s in ClaimStatus} else None


def _apply_filters(claims: list[ExpenseClaim], f: dict) -> list[ExpenseClaim]:
    """Filter claims by the (already-validated-ish) filter dict. Bad values are ignored."""
    out = claims
    if f.get("status") in {s.value for s in ClaimStatus}:
        out = [c for c in out if c.status == f["status"]]
    if f.get("category_id"):
        out = [c for c in out if c.category_id == f["category_id"]]
    if f.get("date_from"):
        try:
            d0 = date.fromisoformat(f["date_from"])
            out = [c for c in out if c.expense_date >= d0]
        except ValueError:
            pass
    if f.get("date_to"):
        try:
            d1 = date.fromisoformat(f["date_to"])
            out = [c for c in out if c.expense_date <= d1]
        except ValueError:
            pass
    for key, op in (("amount_min", "ge"), ("amount_max", "le")):
        if f.get(key):
            try:
                v = Decimal(str(f[key]))
                out = [c for c in out if (c.amount >= v if op == "ge" else c.amount <= v)]
            except (InvalidOperation, TypeError):
                pass
    if f.get("q"):
        ql = f["q"].strip().lower()
        if ql:
            out = [
                c for c in out
                if ql in c.description.lower() or ql in (c.category.name or "").lower()
            ]
    return out


@router.get("/claims")
def my_claims_page(
    request: Request,
    status: str | None = None,
    category_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    amount_min: str | None = None,
    amount_max: str | None = None,
    q: str | None = None,
    sort: str = "date",
    dir: str = "desc",
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    all_claims = _my_claims(db, user)
    active = _valid_status(status)
    # category_id comes from a <select> whose "Any" option is an empty string.
    cat_id = int(category_id) if (category_id and category_id.isdigit()) else None
    filters = {
        "status": active,
        "category_id": cat_id,
        "date_from": date_from,
        "date_to": date_to,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "q": q,
    }
    shown = _apply_filters(all_claims, filters)
    # "advanced" filters = anything beyond the status pill
    filters_active = any(
        filters[k] for k in ("category_id", "date_from", "date_to", "amount_min", "amount_max", "q")
    )
    # Sorting.
    sort_keys = {
        "date": lambda c: c.expense_date,
        "merchant": lambda c: (c.merchant or "").lower(),
        "category": lambda c: (c.category.name if c.category else "").lower(),
        "amount": lambda c: c.amount,
        "status": lambda c: c.status,
    }
    if sort not in sort_keys:
        sort = "date"
    if dir not in ("asc", "desc"):
        dir = "desc"
    shown = sorted(shown, key=sort_keys[sort], reverse=(dir == "desc"))
    # Base querystring (current filters, without sort) for building sort links.
    base_pairs = [
        (k, v) for k, v in (
            ("status", status), ("category_id", category_id), ("date_from", date_from),
            ("date_to", date_to), ("amount_min", amount_min), ("amount_max", amount_max), ("q", q),
        ) if v
    ]
    base_qs = urlencode(base_pairs)
    # Summary of the currently shown claims, totalled per currency (mixing is not summed).
    totals: dict[str, float] = {}
    for c in shown:
        totals[c.currency] = totals.get(c.currency, 0.0) + float(c.amount)
    totals_display = [(cur, f"{amt:,.2f}") for cur, amt in sorted(totals.items())]
    return templates.TemplateResponse(
        request,
        "claims_list.html",
        {
            "user": user,
            "claims": shown,
            "counts": _status_counts(all_claims),
            "total": len(all_claims),
            "shown_count": len(shown),
            "totals": totals_display,
            "active_status": active,
            "categories": db.query(Category).order_by(Category.name).all(),
            "filters": filters,
            "filters_active": filters_active,
            "sort": sort,
            "dir": dir,
            "base_qs": base_qs,
            "saved_views": list_saved_views(db, user),
        },
    )


@router.get("/search")
def search_page(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Full-text-ish search across the user's own claims and (for approvers)
    claims routed to them. Scoped: never returns claims the user cannot view."""
    results = []
    query = (q or "").strip()
    if query:
        ql = query.lower()
        pool: dict[int, ExpenseClaim] = {}
        for c in db.query(ExpenseClaim).filter(ExpenseClaim.submitter_id == user.id).all():
            pool[c.id] = c
        if user.is_approver:
            for c in db.query(ExpenseClaim).filter(ExpenseClaim.approver_id == user.id).all():
                pool[c.id] = c

        def _match(c: ExpenseClaim) -> bool:
            hay = " ".join([
                c.merchant or "",
                c.description or "",
                c.category.name if c.category else "",
                c.status or "",
                str(c.amount),
                c.submitter.full_name if c.submitter else "",
            ]).lower()
            return ql in hay

        results = sorted(
            [c for c in pool.values() if _match(c)],
            key=lambda c: c.created_at,
            reverse=True,
        )
    return templates.TemplateResponse(
        request, "search.html", {"user": user, "q": q, "results": results}
    )


@router.get("/claims/views/new/panel")
def save_view_panel(
    request: Request,
    status: str = "",
    category_id: str = "",
    date_from: str = "",
    date_to: str = "",
    amount_min: str = "",
    amount_max: str = "",
    q: str = "",
    sort: str = "",
    dir: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """HTMX fragment: 'Save view' drawer with a name field + a readable summary
    of the filters currently applied on the Expenses list."""
    summary = []
    if status:
        summary.append(("Status", status.capitalize()))
    if category_id and category_id.isdigit():
        cat = db.get(Category, int(category_id))
        if cat:
            summary.append(("Category", cat.name))
    if date_from:
        summary.append(("From date", date_from))
    if date_to:
        summary.append(("To date", date_to))
    if amount_min:
        summary.append(("Min amount", amount_min))
    if amount_max:
        summary.append(("Max amount", amount_max))
    if q:
        summary.append(("Search", q))
    if sort:
        summary.append(("Sort by", sort.capitalize()))
        summary.append(("Sort order", "Descending" if dir == "desc" else "Ascending"))
    params = {
        "status": status, "category_id": category_id, "date_from": date_from,
        "date_to": date_to, "amount_min": amount_min, "amount_max": amount_max, "q": q,
        "sort": sort, "dir": dir,
    }
    return templates.TemplateResponse(
        request, "_save_view.html", {"user": user, "summary": summary, "params": params}
    )


@router.post("/claims/views")
def save_view(
    name: str = Form(""),
    status: str = Form(""),
    category_id: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    amount_min: str = Form(""),
    amount_max: str = Form(""),
    q: str = Form(""),
    sort: str = Form(""),
    dir: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    params = {
        "status": status, "category_id": category_id, "date_from": date_from,
        "date_to": date_to, "amount_min": amount_min, "amount_max": amount_max, "q": q,
        "sort": sort, "dir": dir,
    }
    try:
        create_saved_view(db, user, name, params)
    except ClaimError as exc:
        return RedirectResponse(f"/claims?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/claims", status_code=303)


@router.post("/claims/views/{view_id}/delete")
def remove_view(
    view_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    delete_saved_view(db, user, view_id)
    return RedirectResponse("/claims", status_code=303)


@router.post("/claims/{claim_id}/currency")
def change_currency(
    claim_id: int,
    currency: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Change a claim's currency (owner + pending only, via update_claim_fields)."""
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None or claim.submitter_id != user.id:
        return RedirectResponse("/claims", status_code=303)
    update_claim_fields(db, claim, user, {"currency": currency})
    return RedirectResponse(f"/claims/{claim_id}", status_code=303)


@router.post("/claims/bulk-withdraw")
def bulk_withdraw(
    claim_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    for cid in claim_ids:
        claim = db.get(ExpenseClaim, cid)
        if claim and claim.submitter_id == user.id and claim.is_pending:
            try:
                withdraw_claim(db, claim, user)
            except ClaimError:
                pass
    return RedirectResponse("/claims", status_code=303)


@router.post("/claims/bulk-export")
def bulk_export(
    claim_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ids = set(claim_ids)
    claims = [c for c in _my_claims(db, user) if c.id in ids]
    return Response(
        content=claims_to_csv(claims),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="selected-claims.csv"'},
    )


@router.get("/my/table")
def my_claims_table(
    request: Request,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """HTMX partial polled by the list so statuses update in near real time."""
    all_claims = _my_claims(db, user)
    active = _valid_status(status)
    shown = [c for c in all_claims if c.status == active] if active else all_claims
    return templates.TemplateResponse(
        request,
        "_my_claims_table.html",
        {"user": user, "claims": shown},
    )


def _report_for_new(db: Session, user: User, report_id: int | None) -> Report | None:
    """The draft report a new expense is being created into (owner-only), or None."""
    if not report_id:
        return None
    report = db.get(Report, report_id)
    if report is None or report.submitter_id != user.id or not report.is_draft:
        return None
    return report


def _categories_for(db: Session, report: Report | None) -> list[Category]:
    """All categories — a report is not tied to a specific approver, so any fit."""
    return db.query(Category).order_by(Category.name).all()


@router.get("/claims/new")
def new_claim_form(
    request: Request,
    report_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    report = _report_for_new(db, user, report_id)
    categories = _categories_for(db, report)
    return templates.TemplateResponse(
        request,
        "claims/new.html",
        {
            "user": user,
            "categories": categories,
            "currencies": CURRENCIES,
            "report": report,
            "report_id": report.id if report else None,
        },
    )


@router.get("/claims/new/panel")
def new_claim_panel(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """HTMX fragment: the create-expense form for the slide-out drawer."""
    categories = db.query(Category).order_by(Category.name).all()
    return templates.TemplateResponse(
        request, "_claim_new.html", {"user": user, "categories": categories, "currencies": CURRENCIES}
    )


@router.post("/claims/scan")
def scan_receipt(
    receipt: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """AI-read an uploaded receipt and return fields to prefill the create form."""
    data = receipt.file.read() if receipt else b""
    mt = receipt_media_type(receipt.filename) if receipt else None
    if not (data and mt):
        return JSONResponse({"status": "unavailable"})
    cats = db.query(Category).order_by(Category.name).all()
    res = extract_receipt_fields(
        receipt_bytes=data, receipt_media_type=mt, categories=[c.name for c in cats]
    )
    if res.get("status") != "ok":
        return JSONResponse({"status": "unavailable"})
    cat_id = None
    sc = (res.get("category") or "").lower()
    for c in cats:
        if c.name.lower() == sc:
            cat_id = c.id
            break
    cur = (res.get("currency") or "").upper()
    cur = cur if cur in CURRENCIES else ""
    return JSONResponse({
        "status": "ok",
        "merchant": res.get("merchant") or "",
        "amount": res.get("amount") or "",
        "currency": cur,
        "expense_date": res.get("date") or "",
        "description": res.get("description") or "",
        "category_id": cat_id,
    })


@router.post("/claims")
def submit_claim(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    category_id: int = Form(...),
    amount: str = Form(...),
    currency: str = Form("USD"),
    merchant: str = Form(""),
    description: str = Form(...),
    expense_date: str = Form(...),
    payment_details: str = Form(...),
    reimbursable: str | None = Form(None),
    receipt: UploadFile | None = File(None),
    report_id: str = Form(None),
):
    report = _report_for_new(db, user, int(report_id) if (report_id or "").isdigit() else None)
    categories = _categories_for(db, report)
    currency = currency if currency in CURRENCIES else "USD"

    def _rerender(error: str):
        return templates.TemplateResponse(
            request,
            "claims/new.html",
            {
                "user": user,
                "categories": categories,
                "currencies": CURRENCIES,
                "report": report,
                "report_id": report.id if report else None,
                "error": error,
                "form": {
                    "category_id": category_id,
                    "amount": amount,
                    "currency": currency,
                    "merchant": merchant,
                    "description": description,
                    "expense_date": expense_date,
                    "payment_details": payment_details,
                    "reimbursable": reimbursable is not None,
                },
            },
            status_code=400,
        )

    category = db.get(Category, category_id)
    if category is None:
        return _rerender("Please choose a valid category")

    try:
        parsed_date = date.fromisoformat(expense_date)
    except ValueError:
        return _rerender("Expense date must be a valid date")

    try:
        receipt_filename, receipt_path = _save_receipt(receipt)
        claim = create_claim(
            db,
            submitter=user,
            category=category,
            amount=amount,
            currency=currency,
            merchant=merchant,
            description=description,
            expense_date=parsed_date,
            payment_details=payment_details,
            reimbursable=reimbursable is not None,
            receipt_filename=receipt_filename,
            receipt_path=receipt_path,
        )
    except ClaimError as exc:
        return _rerender(str(exc))

    # When building a report, honor the category the user picked (it already matches
    # the report's approver) and attach the expense — don't let AI auto-reroute eject it.
    if report is not None:
        claim.report_id = report.id
        db.commit()
        return RedirectResponse(f"/reports/{report.id}", status_code=303)

    notice = _auto_categorize(db, claim, user)
    if notice:
        return RedirectResponse(f"/claims/{claim.id}?msg={quote(notice)}", status_code=303)
    return RedirectResponse("/claims", status_code=303)


@router.post("/claims/{claim_id}/withdraw")
def withdraw(
    claim_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None or claim.submitter_id != user.id:
        # Don't reveal existence of claims that aren't the user's.
        return RedirectResponse("/", status_code=303)
    try:
        withdraw_claim(db, claim, user)
    except ClaimError as exc:
        return RedirectResponse(f"/claims/{claim_id}?error={exc}", status_code=303)
    return RedirectResponse(f"/claims/{claim_id}", status_code=303)


def _receipt_file(receipt: Receipt) -> FileResponse:
    path = os.path.join(settings.upload_dir, receipt.path)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Receipt file missing")
    return FileResponse(path, filename=receipt.filename or receipt.path)


def _safe_dest(return_to: str | None, claim_id: int) -> str:
    """Only accept a same-origin relative path to redirect back to."""
    if return_to and return_to.startswith("/") and not return_to.startswith("//"):
        return return_to
    return f"/claims/{claim_id}"


@router.get("/claims/{claim_id}/receipt")
def claim_receipt(
    claim_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Serve the claim's first receipt (back-compat link/thumbnail)."""
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None or not claim.receipts or not can_view(claim, user):
        raise HTTPException(status_code=404, detail="Receipt not found")
    return _receipt_file(claim.receipts[0])


@router.get("/claims/{claim_id}/receipt/{receipt_id}")
def claim_receipt_one(
    claim_id: int,
    receipt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Serve one specific receipt of a claim."""
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None or not can_view(claim, user):
        raise HTTPException(status_code=404, detail="Receipt not found")
    receipt = db.get(Receipt, receipt_id)
    if receipt is None or receipt.claim_id != claim.id:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return _receipt_file(receipt)


@router.post("/claims/{claim_id}/receipt")
def upload_receipt(
    claim_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    receipt: UploadFile = File(...),
    return_to: str = Form(None),
):
    """Attach ANOTHER receipt to an existing claim (owner + pending). Claims may hold several.

    ``return_to`` (a safe same-site path, e.g. the report the upload was launched
    from) lets the user stay where they were instead of landing on the claim page.
    """
    dest = _safe_dest(return_to, claim_id)
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None or claim.submitter_id != user.id:
        return RedirectResponse("/claims", status_code=303)
    try:
        fn, path = _save_receipt(receipt)
        if path:
            add_receipt(db, claim, user, fn, path)
    except ClaimError as exc:
        return RedirectResponse(f"{dest}?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(dest, status_code=303)


@router.post("/claims/{claim_id}/receipt/{receipt_id}/delete")
def delete_receipt_route(
    claim_id: int,
    receipt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    return_to: str = Form(None),
):
    """Remove one receipt from a claim (owner + pending)."""
    dest = _safe_dest(return_to, claim_id)
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None or claim.submitter_id != user.id:
        return RedirectResponse("/claims", status_code=303)
    try:
        path = delete_receipt(db, claim, user, receipt_id)
    except ClaimError as exc:
        return RedirectResponse(f"{dest}?error={quote(str(exc))}", status_code=303)
    if path:
        fp = os.path.join(settings.upload_dir, path)
        try:
            if os.path.exists(fp):
                os.remove(fp)
        except OSError:
            pass
    return RedirectResponse(dest, status_code=303)


@router.get("/export/my.csv")
def export_my_claims(db: Session = Depends(get_db), user: User = Depends(require_user)):
    csv_text = claims_to_csv(_my_claims(db, user))
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="my-claims.csv"'},
    )
