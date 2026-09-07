"""AI is advisory and degrades gracefully.

Covers contract item (f): without an API key the analysis is "unavailable" and
submitting / deciding still work end to end.
"""
from datetime import date

from app.ai.service import generate_review
from conftest import get_claim, latest_claim_id


def test_generate_review_is_unavailable_without_key():
    review = generate_review(
        amount="45.00",
        currency="USD",
        category="Office",
        description="printer paper and pens",
        expense_date=date(2026, 8, 1),
    )
    assert review["status"] == "unavailable"
    assert review["summary"] is None
    assert review["flag"] is None


def test_ai_panel_renders_without_key(db, world, as_user, submit_claim):
    bob = as_user(world.bob_email)
    submit_claim(bob, world.cats["Office"])
    cid = latest_claim_id(db, world.bob_id)

    alice = as_user(world.alice_email)  # assigned approver
    resp = alice.get(f"/claims/{cid}/ai")
    assert resp.status_code == 200
    # Nothing is cached as a successful review when AI is unavailable.
    assert get_claim(db, cid).ai_status != "ok"


def test_decisions_work_without_ai(db, world, as_user, submit_claim):
    bob = as_user(world.bob_email)
    submit_claim(bob, world.cats["Office"])
    cid = latest_claim_id(db, world.bob_id)

    as_user(world.alice_email).post(f"/claims/{cid}/approve")
    assert get_claim(db, cid).status == "approved"
