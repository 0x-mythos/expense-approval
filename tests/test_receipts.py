"""Multiple receipts per expense: add several, delete, and scoping."""
import io

from conftest import get_claim, latest_claim_id


def _png() -> bytes:
    # Minimal 1x1 PNG.
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )


def _submit(db, world, as_user, submit_claim):
    bob = as_user(world.bob_email)
    submit_claim(bob, world.cats["Office"])
    return bob, latest_claim_id(db, world.bob_id)


def _upload(client, claim_id, name="r.png", return_to=None):
    data = {"return_to": return_to} if return_to else {}
    return client.post(
        f"/claims/{claim_id}/receipt",
        data=data,
        files={"receipt": (name, io.BytesIO(_png()), "image/png")},
        follow_redirects=False,
    )


def test_can_attach_multiple_receipts(db, world, as_user, submit_claim):
    bob, cid = _submit(db, world, as_user, submit_claim)
    _upload(bob, cid)
    _upload(bob, cid)
    _upload(bob, cid)
    assert get_claim(db, cid).receipt_count == 3


def test_upload_return_to_report(db, world, as_user, submit_claim):
    bob, cid = _submit(db, world, as_user, submit_claim)
    resp = _upload(bob, cid, return_to="/reports/7")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/reports/7"


def test_delete_one_receipt(db, world, as_user, submit_claim):
    bob, cid = _submit(db, world, as_user, submit_claim)
    _upload(bob, cid)
    _upload(bob, cid)
    claim = get_claim(db, cid)
    rid = claim.receipts[0].id
    bob.post(f"/claims/{cid}/receipt/{rid}/delete")
    assert get_claim(db, cid).receipt_count == 1


def test_only_owner_can_add_receipt(db, world, as_user, submit_claim):
    bob, cid = _submit(db, world, as_user, submit_claim)
    alice = as_user(world.alice_email)  # approver, not the submitter
    _upload(alice, cid)
    assert get_claim(db, cid).receipt_count == 0
