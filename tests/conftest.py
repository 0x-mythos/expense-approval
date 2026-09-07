"""Test configuration and fixtures.

The environment is prepared BEFORE the app package is imported so that
``app.config.Settings`` (instantiated at import time) picks up a throwaway
SQLite database and no AI key. Tests therefore need neither Postgres nor the
seed data — every test builds exactly the world it needs.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

# 1) Make the project root importable regardless of the pytest invocation dir.
_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# 2) Point the app at a disposable SQLite file and disable AI — must happen
#    before `app.config` is imported.
_TEST_DB = pathlib.Path(tempfile.gettempdir()) / "expense_approval_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret"
# Force AI off in tests. Set (not pop) so this overrides any key in a local .env
# file — pydantic-settings ranks real env vars above .env values.
os.environ["ANTHROPIC_API_KEY"] = ""

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Category, ExpenseClaim, User  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSWORD = "demo1234"


@pytest.fixture(autouse=True)
def _fresh_schema():
    """Give every test an empty schema."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def world(db):
    """Users + categories matching the seed's routing, created directly.

    Routing: Office / Software-Subscriptions / Other -> Alice;
             Travel / Client Entertainment          -> Carol.
    Bob is an employee only.
    """

    def mk_user(email: str, name: str, approver: bool) -> User:
        u = User(
            email=email,
            full_name=name,
            password_hash=hash_password(PASSWORD),
            is_employee=True,
            is_approver=approver,
        )
        db.add(u)
        return u

    alice = mk_user("alice@example.com", "Alice", True)
    carol = mk_user("carol@example.com", "Carol", True)
    bob = mk_user("bob@example.com", "Bob", False)
    db.flush()

    routing = {
        "Office": alice,
        "Software/Subscriptions": alice,
        "Other": alice,
        "Travel": carol,
        "Client Entertainment": carol,
    }
    cats: dict[str, int] = {}
    for name, approver in routing.items():
        c = Category(name=name, approver_id=approver.id)
        db.add(c)
        db.flush()
        cats[name] = c.id

    db.commit()
    return SimpleNamespace(
        alice_email=alice.email,
        carol_email=carol.email,
        bob_email=bob.email,
        alice_id=alice.id,
        carol_id=carol.id,
        bob_id=bob.id,
        cats=cats,
    )


@pytest.fixture
def as_user():
    """Return a factory that yields a TestClient already logged in as ``email``.

    A separate client (and cookie jar) per user keeps sessions isolated.
    """

    def _factory(email: str, password: str = PASSWORD) -> TestClient:
        client = TestClient(app)
        resp = client.post("/login", data={"email": email, "password": password})
        assert resp.status_code == 200, f"login failed for {email}: {resp.status_code}"
        return client

    return _factory


@pytest.fixture
def submit_claim():
    """POST a new claim as the given (logged-in) client."""

    def _submit(
        client: TestClient,
        category_id: int,
        amount: str = "45.00",
        description: str = "printer paper and pens",
        expense_date: str = "2026-08-01",
        payment_details: str = "IBAN UA00 0000",
        **kwargs,
    ):
        data = {
            "category_id": category_id,
            "amount": amount,
            "description": description,
            "expense_date": expense_date,
            "payment_details": payment_details,
        }
        return client.post("/claims", data=data, **kwargs)

    return _submit


def latest_claim_id(db, submitter_id: int) -> int:
    # End our read snapshot so we observe rows committed by the HTTP sessions.
    db.rollback()
    claim = (
        db.query(ExpenseClaim)
        .filter(ExpenseClaim.submitter_id == submitter_id)
        .order_by(ExpenseClaim.id.desc())
        .first()
    )
    assert claim is not None, "no claim found for submitter"
    return claim.id


def get_claim(db, claim_id: int) -> ExpenseClaim:
    db.rollback()
    return db.get(ExpenseClaim, claim_id)


def audit_count(db, claim_id: int) -> int:
    from app.models import AuditLog

    db.rollback()
    return db.query(AuditLog).filter(AuditLog.claim_id == claim_id).count()
