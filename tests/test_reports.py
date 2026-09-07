"""Expensify-style report lifecycle.

draft -> submit -> outstanding -> approve / reject / retract -> paid, with the
decision cascading to the report's member claims and strict submitter/approver
scoping. Mirrors the flow analysed on new.expensify.com (type:expense-report).
"""
from conftest import get_claim, latest_claim_id

from app.models import Report


def _report_of(db, submitter_id: int) -> Report:
    db.rollback()
    return (
        db.query(Report)
        .filter(Report.submitter_id == submitter_id)
        .order_by(Report.id.desc())
        .first()
    )


def _make_draft(db, world, as_user, submit_claim):
    """Two pending Office claims (both routed to Alice) bundled into a draft report."""
    bob = as_user(world.bob_email)
    submit_claim(bob, world.cats["Office"], amount="45.00")
    c1 = latest_claim_id(db, world.bob_id)
    submit_claim(bob, world.cats["Office"], amount="60.00")
    c2 = latest_claim_id(db, world.bob_id)
    bob.post("/reports", data={"name": "", "claim_ids": [c1, c2]})
    return bob, _report_of(db, world.bob_id), c1, c2


def test_create_report_starts_as_draft(db, world, as_user, submit_claim):
    bob, r, c1, c2 = _make_draft(db, world, as_user, submit_claim)
    assert r.status == "draft"
    assert r.name.startswith("Expense Report ")
    assert {c.id for c in r.claims} == {c1, c2}


def test_draft_hidden_from_queue_until_submitted(db, world, as_user, submit_claim):
    bob, r, c1, c2 = _make_draft(db, world, as_user, submit_claim)
    alice = as_user(world.alice_email)
    assert f"/reports/{r.id}" not in alice.get("/queue").text  # draft is invisible

    bob.post(f"/reports/{r.id}/submit")
    db.rollback()
    r2 = db.get(Report, r.id)
    assert r2.status == "outstanding"
    assert r2.submitted_at is not None
    assert f"/reports/{r.id}" in alice.get("/queue").text  # now awaiting decision


def test_approve_report_cascades_to_claims(db, world, as_user, submit_claim):
    bob, r, c1, c2 = _make_draft(db, world, as_user, submit_claim)
    bob.post(f"/reports/{r.id}/submit")
    as_user(world.alice_email).post(f"/reports/{r.id}/approve")

    db.rollback()
    assert db.get(Report, r.id).status == "approved"
    assert get_claim(db, c1).status == "approved"
    assert get_claim(db, c2).status == "approved"


def test_reject_needs_comment_then_cascades(db, world, as_user, submit_claim):
    bob, r, c1, c2 = _make_draft(db, world, as_user, submit_claim)
    bob.post(f"/reports/{r.id}/submit")
    alice = as_user(world.alice_email)

    resp = alice.post(f"/reports/{r.id}/reject", data={"comment": ""}, follow_redirects=False)
    assert resp.status_code == 303
    db.rollback()
    assert db.get(Report, r.id).status == "outstanding"  # blocked, unchanged

    alice.post(f"/reports/{r.id}/reject", data={"comment": "Missing receipts."})
    db.rollback()
    r2 = db.get(Report, r.id)
    assert r2.status == "rejected"
    assert r2.decision_comment == "Missing receipts."
    assert get_claim(db, c1).status == "rejected"


def test_retract_returns_to_draft(db, world, as_user, submit_claim):
    bob, r, c1, c2 = _make_draft(db, world, as_user, submit_claim)
    bob.post(f"/reports/{r.id}/submit")
    bob.post(f"/reports/{r.id}/retract")

    db.rollback()
    r2 = db.get(Report, r.id)
    assert r2.status == "draft"
    assert r2.submitted_at is None


def test_mark_paid_only_after_approval(db, world, as_user, submit_claim):
    bob, r, c1, c2 = _make_draft(db, world, as_user, submit_claim)
    bob.post(f"/reports/{r.id}/submit")
    alice = as_user(world.alice_email)

    # too early: outstanding cannot be marked paid
    resp = alice.post(f"/reports/{r.id}/mark-paid", follow_redirects=False)
    assert resp.status_code == 303
    db.rollback()
    assert db.get(Report, r.id).status == "outstanding"

    alice.post(f"/reports/{r.id}/approve")
    alice.post(f"/reports/{r.id}/mark-paid")
    db.rollback()
    assert db.get(Report, r.id).status == "paid"


def test_report_detail_forbidden_for_unrelated_user(db, world, as_user, submit_claim):
    bob, r, c1, c2 = _make_draft(db, world, as_user, submit_claim)
    carol = as_user(world.carol_email)  # neither submitter nor this report's approver
    resp = carol.get(f"/reports/{r.id}", follow_redirects=False)
    assert resp.status_code == 403


def test_create_new_expense_into_report(db, world, as_user, submit_claim):
    bob, r, c1, c2 = _make_draft(db, world, as_user, submit_claim)
    # Create a brand-new expense straight into the draft report (same approver: Office -> Alice).
    resp = bob.post(
        "/claims",
        data={
            "category_id": world.cats["Office"],
            "amount": "12.34",
            "description": "Lunch with the team",
            "expense_date": "2026-09-05",
            "payment_details": "IBAN UA00",
            "report_id": str(r.id),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/reports/{r.id}"
    db.rollback()
    assert len(db.get(Report, r.id).claims) == 3  # c1, c2 + the new one


def test_report_mixes_categories_and_any_approver_decides(db, world, as_user, submit_claim):
    bob = as_user(world.bob_email)
    submit_claim(bob, world.cats["Office"], amount="10.00")   # routed to Alice
    c1 = latest_claim_id(db, world.bob_id)
    submit_claim(bob, world.cats["Travel"], amount="20.00")   # routed to Carol
    c2 = latest_claim_id(db, world.bob_id)

    # Mixed-category expenses can now be bundled into one report.
    bob.post("/reports", data={"name": "Mixed", "claim_ids": [c1, c2]})
    r = _report_of(db, world.bob_id)
    assert r.status == "draft"
    bob.post(f"/reports/{r.id}/submit")

    # Carol (an approver) can approve the whole report even though c1 was routed to Alice.
    as_user(world.carol_email).post(f"/reports/{r.id}/approve")
    db.rollback()
    r2 = db.get(Report, r.id)
    assert r2.status == "approved"
    assert r2.approver_id == world.carol_id  # records who actually approved
    assert get_claim(db, c1).status == "approved"
    assert get_claim(db, c2).status == "approved"


def test_groups_by_category_with_subtotal(db, world, as_user, submit_claim):
    bob, r, c1, c2 = _make_draft(db, world, as_user, submit_claim)
    db.rollback()
    groups = db.get(Report, r.id).groups
    assert len(groups) == 1  # both claims are Office
    assert groups[0]["category"] == "Office"
    assert groups[0]["subtotal"] == 105.0
