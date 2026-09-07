"""Authorization / data scoping — enforced server-side, not just in templates.

Covers contract items (d) scoping and (e) only the assigned approver decides.
"""
from conftest import get_claim, latest_claim_id


def _bob_office_claim(db, world, as_user, submit_claim) -> int:
    bob = as_user(world.bob_email)
    submit_claim(bob, world.cats["Office"])  # -> routed to Alice
    return latest_claim_id(db, world.bob_id)


def test_owner_can_view_own_claim(db, world, as_user, submit_claim):
    cid = _bob_office_claim(db, world, as_user, submit_claim)
    bob = as_user(world.bob_email)
    assert bob.get(f"/claims/{cid}").status_code == 200


def test_assigned_approver_can_view(db, world, as_user, submit_claim):
    cid = _bob_office_claim(db, world, as_user, submit_claim)
    alice = as_user(world.alice_email)
    assert alice.get(f"/claims/{cid}").status_code == 200


def test_unassigned_approver_cannot_view(db, world, as_user, submit_claim):
    cid = _bob_office_claim(db, world, as_user, submit_claim)
    carol = as_user(world.carol_email)  # approver, but not for Office
    assert carol.get(f"/claims/{cid}").status_code == 403


def test_non_approver_forbidden_from_queue(world, as_user):
    bob = as_user(world.bob_email)  # employee only
    assert bob.get("/queue").status_code == 403


def test_unassigned_approver_cannot_approve(db, world, as_user, submit_claim):
    cid = _bob_office_claim(db, world, as_user, submit_claim)
    carol = as_user(world.carol_email)

    resp = carol.post(f"/claims/{cid}/approve", follow_redirects=False)
    assert resp.status_code == 303  # blocked with an error redirect
    assert get_claim(db, cid).status == "pending"  # not approved by the wrong approver
