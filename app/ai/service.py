"""AI review helper: a short summary + an inconsistency flag for an approver.

Design guarantees (the AI is advisory, never authoritative):
  * It NEVER decides a claim and NEVER blocks the approver.
  * It is called lazily (only when an approver opens a claim), so submitting and
    deciding never wait on it.
  * ``generate_review`` never raises: on a missing key, timeout, or any API error
    it returns ``status="unavailable"`` and the UI shows a neutral notice.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from datetime import date
from decimal import Decimal
from typing import Any

from app.config import settings

_RECEIPT_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}


def receipt_media_type(filename: str | None) -> str | None:
    """Return an Anthropic-supported media type for a receipt filename, or None."""
    if not filename:
        return None
    return _RECEIPT_MEDIA_TYPES.get(os.path.splitext(filename)[1].lower())

log = logging.getLogger("expense.ai")

# --- Safety budget: cap AI calls so a bug/hang can't burn tokens ---
_budget_lock = threading.Lock()
_call_times: list[float] = []


def _budget_ok() -> bool:
    """Return False (and skip the API call) if the per-minute or per-day cap is hit."""
    now = time.time()
    with _budget_lock:
        while _call_times and _call_times[0] < now - 86400:
            _call_times.pop(0)
        per_min = sum(1 for t in _call_times if t > now - 60)
        if len(_call_times) >= settings.ai_max_calls_per_day:
            log.warning("AI daily budget reached (%s) — skipping call", settings.ai_max_calls_per_day)
            return False
        if per_min >= settings.ai_max_calls_per_min:
            log.warning("AI per-minute budget reached (%s) — skipping call", settings.ai_max_calls_per_min)
            return False
        _call_times.append(now)
        return True

_SYSTEM = (
    "You help an expense approver quickly review a reimbursement claim. "
    "Respond with ONLY a compact JSON object (no markdown, no prose) with keys: "
    '"summary" (string, 1-2 plain-language sentences describing the claim), '
    '"inconsistent" (boolean), "reason" (string, short; empty when consistent), '
    '"suggested_category" (string; the best-fitting category from the provided list), '
    'and "category_reason" (string, short; why that category fits). '
    "Mark it inconsistent when the amount, category and description do not align — "
    "for example category 'Office' but a description about a flight to London. "
    "If a receipt image or PDF is attached, READ IT (merchant, line items, total, date) "
    "and use it to pick the best category and to check that the claimed amount, category "
    "and description match what the receipt actually shows; note any mismatch in the reason. "
    "Do not judge whether to approve; only describe, categorize and flag."
)

_UNAVAILABLE = {
    "status": "unavailable",
    "summary": None,
    "flag": None,
    "flag_reason": None,
    "suggested_category": None,
    "category_reason": None,
}


def generate_review(
    *,
    amount: Decimal | float | str,
    currency: str,
    category: str,
    description: str,
    expense_date: date,
    merchant: str | None = None,
    categories: list[str] | None = None,
    receipt_bytes: bytes | None = None,
    receipt_media_type: str | None = None,
) -> dict[str, Any]:
    """Return {status, summary, flag, flag_reason, suggested_category, category_reason}.

    If a receipt (image/PDF bytes + media type) is given, it is attached so the model
    can read it. Never raises — degrades to status="unavailable" on any failure; if the
    receipt attachment itself fails, retries once text-only rather than losing the review.
    """
    if not settings.anthropic_api_key:
        return dict(_UNAVAILABLE)
    if not _budget_ok():
        return dict(_UNAVAILABLE)

    cat_list = ", ".join(categories) if categories else "(not provided)"
    has_receipt = bool(receipt_bytes and receipt_media_type)
    prompt = (
        f"Available categories: {cat_list}\n"
        f"Chosen category: {category}\n"
        f"Amount: {amount} {currency}\n"
        f"Merchant: {merchant or '(not provided)'}\n"
        f"Expense date: {expense_date.isoformat()}\n"
        f"Description: {description}\n"
        + ("A receipt is attached above — read it and factor it in." if has_receipt else "No receipt attached.")
    )

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        api = client.with_options(timeout=settings.ai_timeout_seconds, max_retries=0)

        def _call(content):
            return api.messages.create(
                model=settings.ai_model, max_tokens=400, system=_SYSTEM,
                messages=[{"role": "user", "content": content}],
            )

        text_block = {"type": "text", "text": prompt}
        if has_receipt:
            b64 = base64.standard_b64encode(receipt_bytes).decode("utf-8")
            if receipt_media_type == "application/pdf":
                attach = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
            else:
                attach = {"type": "image", "source": {"type": "base64", "media_type": receipt_media_type, "data": b64}}
            try:
                response = _call([attach, text_block])
            except Exception as attach_err:  # noqa: BLE001 — keep review even if the receipt won't attach
                log.warning("AI receipt attach failed (%s); retrying text-only", attach_err)
                response = _call([text_block])
        else:
            response = _call([text_block])

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        data = _parse_json(text)
        return {
            "status": "ok",
            "summary": (data.get("summary") or "").strip() or None,
            "flag": bool(data.get("inconsistent")),
            "flag_reason": (data.get("reason") or "").strip() or None,
            "suggested_category": (data.get("suggested_category") or "").strip() or None,
            "category_reason": (data.get("category_reason") or "").strip() or None,
        }
    except Exception as exc:  # noqa: BLE001 — degrade on ANY failure
        log.warning("AI review unavailable: %s", exc)
        return dict(_UNAVAILABLE)


_CHAT_SYSTEM = (
    "You are a helper assistant for an expense approver or submitter looking at one "
    "specific expense claim. Answer questions about THIS claim only: explain why its "
    "category fits or doesn't, point out issues (e.g. missing receipt, amount/category "
    "mismatch), and clarify claim details. Be concise (a few sentences). "
    "You NEVER approve, reject, or decide a claim. "
    "When the user asks to change a field (category, amount, description, date, payment "
    "details), use the update_claim tool to make the edit, then confirm briefly what you "
    "changed. If the tool reports it is not allowed (e.g. the user is not the submitter, or "
    "the claim is not pending), explain that plainly and do not retry."
)

# Tool the assistant can call to edit the claim. The actual DB write + permission
# checks happen in the caller-provided executor (services.update_claim_fields).
_UPDATE_TOOL = {
    "name": "update_claim",
    "description": (
        "Edit one or more fields of THIS expense claim on behalf of its submitter. "
        "Only include the fields that should change."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "New category name; must be one of the available categories"},
            "amount": {"type": "number", "description": "New amount in USD, greater than 0"},
            "merchant": {"type": "string", "description": "Vendor / store name where the money was spent"},
            "description": {"type": "string"},
            "expense_date": {"type": "string", "description": "YYYY-MM-DD"},
            "reimbursable": {"type": "boolean", "description": "True if the company reimburses the submitter (out-of-pocket)"},
            "payment_details": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

_CHAT_UNAVAILABLE: dict[str, Any] = {"status": "unavailable", "answer": None}


def answer_question(*, question: str, claim_context: dict[str, Any]) -> dict[str, Any]:
    """Answer a free-form question about one claim, grounded in ``claim_context``.

    Never raises — degrades to status="unavailable" on any failure (including a
    missing API key), matching ``generate_review``'s guarantees.
    """
    if not settings.anthropic_api_key:
        return dict(_CHAT_UNAVAILABLE)
    if not _budget_ok():
        return dict(_CHAT_UNAVAILABLE)

    context_json = json.dumps(claim_context, default=str)
    prompt = f"Claim context (JSON): {context_json}\n\nQuestion: {question}"

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.with_options(
            timeout=settings.ai_timeout_seconds, max_retries=0
        ).messages.create(
            model=settings.ai_model,
            max_tokens=500,
            system=_CHAT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return {"status": "ok", "answer": text or None}
    except Exception as exc:  # noqa: BLE001 — degrade on ANY failure
        log.warning("AI assistant unavailable: %s", exc)
        return dict(_CHAT_UNAVAILABLE)


def chat_with_actions(
    *,
    question: str,
    claim_context: dict[str, Any],
    categories: list[str] | None = None,
    allow_edit: bool = False,
    tool_executor=None,
) -> dict[str, Any]:
    """Chat about one claim, optionally editing it via the update_claim tool.

    ``tool_executor(changes: dict) -> dict`` performs the actual edit (with its own
    permission checks) and returns a result dict. Never raises — degrades to
    status="unavailable" on missing key / timeout / any error.
    """
    if not settings.anthropic_api_key:
        return dict(_CHAT_UNAVAILABLE)
    if not _budget_ok():
        return dict(_CHAT_UNAVAILABLE)

    cat_list = ", ".join(categories) if categories else "(not provided)"
    context_json = json.dumps(claim_context, default=str)
    prompt = (
        f"Available categories: {cat_list}\n"
        f"Claim context (JSON): {context_json}\n\n"
        f"User: {question}"
    )

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        api = client.with_options(timeout=settings.ai_timeout_seconds, max_retries=0)

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        kwargs: dict[str, Any] = {
            "model": settings.ai_model,
            "max_tokens": 700,
            "system": _CHAT_SYSTEM,
            "messages": messages,
        }
        if allow_edit and tool_executor is not None:
            kwargs["tools"] = [_UPDATE_TOOL]

        response = api.messages.create(**kwargs)

        for _ in range(3):  # bounded tool-use loop
            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                break
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for tu in tool_uses:
                if tu.name == "update_claim" and allow_edit and tool_executor is not None:
                    out = tool_executor(dict(tu.input))
                else:
                    out = {"error": "Editing is not available for this claim."}
                results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(out, default=str)}
                )
            messages.append({"role": "user", "content": results})
            response = api.messages.create(**kwargs)

        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
        return {"status": "ok", "answer": text or None}
    except Exception as exc:  # noqa: BLE001 — degrade on ANY failure
        log.warning("AI assistant unavailable: %s", exc)
        return dict(_CHAT_UNAVAILABLE)


def _parse_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction; falls back to treating the text as a summary."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {"summary": text, "inconsistent": False, "reason": ""}


def extract_receipt_fields(
    *,
    receipt_bytes: bytes,
    receipt_media_type: str,
    categories: list[str] | None = None,
) -> dict:
    """Read a receipt image/PDF and extract expense fields to prefill the form.

    Returns {"status": "ok"|"unavailable", "merchant", "amount", "date",
    "description", "category"}. Never raises — degrades to unavailable.
    """
    if not settings.anthropic_api_key:
        return {"status": "unavailable"}
    if not _budget_ok():
        return {"status": "unavailable"}
    cat_list = ", ".join(categories) if categories else "(not provided)"
    system = (
        "You extract structured fields from a receipt image or PDF for an expense form. "
        "Respond with ONLY a compact JSON object (no markdown) with keys: "
        '"merchant" (string), "amount" (number, the grand total), '
        '"currency" (the 3-letter ISO code shown on the receipt, e.g. USD, EUR, GBP, PLN, UAH), '
        '"date" (YYYY-MM-DD), '
        '"description" (a short human summary of what was bought), '
        f'"category" (the single best fit from this list: {cat_list}). '
        "Use empty string or null for anything you cannot read."
    )
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        api = client.with_options(timeout=settings.ai_timeout_seconds, max_retries=0)
        b64 = base64.standard_b64encode(receipt_bytes).decode("utf-8")
        if receipt_media_type == "application/pdf":
            attach = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
        else:
            attach = {"type": "image", "source": {"type": "base64", "media_type": receipt_media_type, "data": b64}}
        resp = api.messages.create(
            model=settings.ai_model,
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": [attach, {"type": "text", "text": "Extract the fields as JSON."}]}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        data = _parse_json(text)
        amount = data.get("amount")
        return {
            "status": "ok",
            "merchant": (str(data.get("merchant") or "")).strip(),
            "amount": (str(amount) if amount not in (None, "") else ""),
            "currency": (str(data.get("currency") or "")).strip().upper(),
            "date": (str(data.get("date") or "")).strip(),
            "description": (str(data.get("description") or "")).strip(),
            "category": (str(data.get("category") or "")).strip(),
        }
    except Exception as exc:  # noqa: BLE001 — degrade on ANY failure
        log.warning("AI receipt extract unavailable: %s", exc)
        return {"status": "unavailable"}
