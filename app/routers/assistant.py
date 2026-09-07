"""Per-claim AI chat assistant.

Answers questions about one claim and — when the submitter asks, and the claim is
still pending — can edit its fields via the update_claim tool. All edits go through
``services.update_claim_fields`` (permission checks + audit). The AI never approves,
rejects, or decides; it never raises (degrades to "unavailable").
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from app.ai.service import chat_with_actions
from app.database import get_db
from app.dependencies import require_user
from app.models import Category, ExpenseClaim, User
from app.services import can_view, update_claim_fields
from app.templating import templates

router = APIRouter()


@router.post("/claims/{claim_id}/assistant")
def ask_assistant(
    claim_id: int,
    request: Request,
    question: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    if not can_view(claim, user):
        raise HTTPException(status_code=403, detail="You cannot view this claim")

    claim_context = {
        "id": claim.id,
        "category": claim.category.name,
        "amount": claim.amount,
        "currency": claim.currency,
        "merchant": claim.merchant,
        "expense_date": claim.expense_date,
        "description": claim.description,
        "payment_details": claim.payment_details,
        "reimbursable": claim.reimbursable,
        "has_receipt": claim.has_receipt,
        "status": claim.status,
        "ai_summary": claim.ai_summary,
        "ai_flag": claim.ai_flag,
        "ai_flag_reason": claim.ai_flag_reason,
    }
    categories = [c.name for c in db.query(Category).order_by(Category.name).all()]
    # Only the submitter can edit, and only while pending.
    allow_edit = claim.submitter_id == user.id and claim.is_pending

    def _executor(changes: dict) -> dict:
        return update_claim_fields(db, claim, user, changes)

    result = chat_with_actions(
        question=question,
        claim_context=claim_context,
        categories=categories,
        allow_edit=allow_edit,
        tool_executor=_executor,
    )

    return templates.TemplateResponse(
        request, "_ai_chat_message.html", {"question": question, "review": result, "user": user}
    )
