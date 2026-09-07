"""Bulk report actions: submit / delete / duplicate."""
from conftest import get_claim, latest_claim_id

from app.models import ExpenseClaim, Report


def _draft(db, world, as_user, submit_claim):
    bob = as_user(world.bob_email)
    submit_claim(bob, world.cats["Office"], amount="10.00")
    c1 = latest_claim_id(db, world.bob_id)
    bob.post("/reports", data={"name": "R", "claim_ids": [c1]})
    db.rollback()
    r = db.query(Report).filter(Report.submitter_id == world.bob_id).order_by(Report.id.desc()).first()
    return bob, r, c1


def test_bulk_submit_drafts(db, world, as_user, submit_claim):
    bob, r, c1 = _draft(db, world, as_user, submit_claim)
    bob.post("/reports/bulk-submit", data={"report_ids": [r.id]})
    db.rollback()
    assert db.get(Report, r.id).status == "outstanding"


def test_bulk_delete_draft_unlinks_expenses(db, world, as_user, submit_claim):
    bob, r, c1 = _draft(db, world, as_user, submit_claim)
    rid = r.id
    bob.post("/reports/bulk-delete", data={"report_ids": [rid]})
    db.rollback()
    assert db.get(Report, rid) is None            # report gone
    assert get_claim(db, c1).report_id is None    # expense returned to the free pool
    assert get_claim(db, c1).status == "pending"


def test_bulk_delete_ignores_submitted(db, world, as_user, submit_claim):
    bob, r, c1 = _draft(db, world, as_user, submit_claim)
    bob.post(f"/reports/{r.id}/submit")
    bob.post("/reports/bulk-delete", data={"report_ids": [r.id]})
    db.rollback()
    assert db.get(Report, r.id) is not None        # not deleted (only drafts can be)
    assert db.get(Report, r.id).status == "outstanding"


def test_bulk_duplicate_makes_new_draft(db, world, as_user, submit_claim):
    bob, r, c1 = _draft(db, world, as_user, submit_claim)
    before = db.query(Report).count()
    bob.post("/reports/bulk-duplicate", data={"report_ids": [r.id]})
    db.rollback()
    assert db.query(Report).count() == before + 1
    dup = db.query(Report).order_by(Report.id.desc()).first()
    assert dup.status == "draft"
    assert dup.name.endswith("(copy)")
    assert len(dup.claims) == 1                     # expense cloned into the copy
