"""Approver decisions and withdrawal rules.

Covers contract items (a) reject needs a comment, (b) reject with comment,
(c) withdraw only while pending, and (h) audit grows per action.
"""
from conftest import audit_count, get_claim, latest_claim_id


def _office_claim(db, world, as_user, submit_claim) -> int:
    bob = as_user(world.bob_email)
    submit_claim(bob, world.cats["Office"])
    return latest_claim_id(db, world.bob_id)


def test_reject_without_comment_is_blocked(db, world, as_user, submit_claim):
    cid = _office_claim(db, world, as_user, submit_claim)
    alice = as_user(world.alice_email)

    resp = alice.post(f"/claims/{cid}/reject", data={"comment": ""}, follow_redirects=False)
    assert resp.status_code == 303  # redirected back with an error

    claim = get_claim(db, cid)
    assert claim.status == "pending"  # unchanged
    assert claim.decision_comment is None
    assert audit_count(db, cid) == 1  # only the submit row


def test_reject_with_comment_sets_rejected(db, world, as_user, submit_claim):
    cid = _office_claim(db, world, as_user, submit_claim)
    alice = as_user(world.alice_email)

    alice.post(f"/claims/{cid}/reject", data={"comment": "Out of policy — no receipt."})

    claim = get_claim(db, cid)
    assert claim.status == "rejected"
    assert claim.decision_comment == "Out of policy — no receipt."
    assert audit_count(db, cid) == 2


def test_approve_sets_approved(db, world, as_user, submit_claim):
    cid = _office_claim(db, world, as_user, submit_claim)
    alice = as_user(world.alice_email)

    alice.post(f"/claims/{cid}/approve")

    claim = get_claim(db, cid)
    assert claim.status == "approved"
    assert claim.decided_by_id == world.alice_id
    assert audit_count(db, cid) == 2


def test_owner_can_withdraw_pending(db, world, as_user, submit_claim):
    cid = _office_claim(db, world, as_user, submit_claim)
    bob = as_user(world.bob_email)

    bob.post(f"/claims/{cid}/withdraw")

    assert get_claim(db, cid).status == "withdrawn"


def test_cannot_withdraw_after_approval(db, world, as_user, submit_claim):
    cid = _office_claim(db, world, as_user, submit_claim)
    as_user(world.alice_email).post(f"/claims/{cid}/approve")

    bob = as_user(world.bob_email)
    bob.post(f"/claims/{cid}/withdraw", follow_redirects=False)

    assert get_claim(db, cid).status == "approved"  # still approved, not withdrawn
