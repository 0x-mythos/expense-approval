"""Submission + category-based routing + audit trail on submit.

Covers contract items (g) routing and part of (h) audit growth.
"""
from conftest import audit_count, get_claim, latest_claim_id


def test_submit_creates_pending_and_routes_office_to_alice(
    db, world, as_user, submit_claim
):
    bob = as_user(world.bob_email)
    resp = submit_claim(bob, world.cats["Office"])
    assert resp.status_code == 200  # 303 -> "/" followed

    claim = get_claim(db, latest_claim_id(db, world.bob_id))
    assert claim.status == "pending"
    assert claim.approver_id == world.alice_id


def test_submit_routes_travel_to_carol(db, world, as_user, submit_claim):
    bob = as_user(world.bob_email)
    submit_claim(bob, world.cats["Travel"], description="taxi to airport")

    claim = get_claim(db, latest_claim_id(db, world.bob_id))
    assert claim.approver_id == world.carol_id


def test_submit_writes_one_audit_row(db, world, as_user, submit_claim):
    bob = as_user(world.bob_email)
    submit_claim(bob, world.cats["Office"])

    cid = latest_claim_id(db, world.bob_id)
    assert audit_count(db, cid) == 1
