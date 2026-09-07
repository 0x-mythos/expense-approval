"""CSV export for claims. Columns mirror a relevant subset of Expensify's
expense-level export (Date, Merchant, Amount, Category, Status, approver, dates…).
"""
import csv
import io

from app.models import ExpenseClaim

_HEADER = [
    "Claim ID",
    "Date",
    "Merchant",
    "Amount",
    "Currency",
    "Category",
    "Description",
    "Transaction Type",
    "Reimbursable",
    "Status",
    "Submitter Name",
    "Submitter Email",
    "Approver Name",
    "Approver Email",
    "Decision Comment",
    "Submitted Date",
    "Decided Date",
    "Has Receipt",
]


def claims_to_csv(claims: list[ExpenseClaim]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_HEADER)
    for c in claims:
        writer.writerow(
            [
                c.id,
                c.expense_date.isoformat() if c.expense_date else "",
                c.merchant or "",
                f"{c.amount}",
                c.currency,
                c.category.name if c.category else "",
                c.description,
                "Cash",  # payment method fixed in the MVP
                "Y" if c.reimbursable else "N",
                c.status,
                c.submitter.full_name if c.submitter else "",
                c.submitter.email if c.submitter else "",
                c.approver.full_name if c.approver else "",
                c.approver.email if c.approver else "",
                c.decision_comment or "",
                c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
                c.decided_at.strftime("%Y-%m-%d") if c.decided_at else "",
                "Y" if c.has_receipt else "N",
            ]
        )
    return out.getvalue()
