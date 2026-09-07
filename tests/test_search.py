"""Search feature tests."""


def test_search_finds_matching_claim(db, world, as_user, submit_claim):
    """Search should find a claim by description."""
    bob = as_user(world.bob_email)
    # Submit a claim with a distinctive description
    submit_claim(bob, world.cats["Office"], description="office supplies from Staples")

    bob = as_user(world.bob_email)  # fresh client
    resp = bob.get("/search?q=Staples")
    assert resp.status_code == 200
    assert "Staples" in resp.text


def test_search_no_match(db, world, as_user, submit_claim):
    """Search with non-matching query should return 200 with no results."""
    bob = as_user(world.bob_email)
    submit_claim(bob, world.cats["Office"], description="office supplies")

    bob = as_user(world.bob_email)  # fresh client
    resp = bob.get("/search?q=zzzznomatch")
    assert resp.status_code == 200
    assert "No expenses match" in resp.text
