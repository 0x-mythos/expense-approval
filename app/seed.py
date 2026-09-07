"""Idempotent demo seed: users, category -> approver routing, and sample claims.

Run after migrations: ``python -m app.seed``. Safe to run repeatedly.
All demo users share the password ``demo1234``.
"""
from datetime import date

from app.database import SessionLocal
from app.models import Category, ExpenseClaim, Report, User
from app.security import hash_password
from app.services import create_claim, create_report, submit_report, approve_report, mark_paid_report

DEMO_PASSWORD = "demo1234"


def _get_or_create_user(db, email: str, full_name: str, is_approver: bool) -> User:
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user
    user = User(
        email=email,
        full_name=full_name,
        password_hash=hash_password(DEMO_PASSWORD),
        is_employee=True,
        is_approver=is_approver,
    )
    db.add(user)
    db.flush()
    return user


def _get_or_create_category(db, name: str, approver: User) -> Category:
    cat = db.query(Category).filter(Category.name == name).first()
    if cat:
        if cat.approver_id is None:
            cat.approver_id = approver.id
        return cat
    cat = Category(name=name, approver_id=approver.id)
    db.add(cat)
    db.flush()
    return cat


def run() -> None:
    db = SessionLocal()
    try:
        alice = _get_or_create_user(db, "alice@example.com", "Alice Approver", is_approver=True)
        carol = _get_or_create_user(db, "carol@example.com", "Carol Finance", is_approver=True)
        bob = _get_or_create_user(db, "bob@example.com", "Bob Employee", is_approver=False)
        db.flush()

        office = _get_or_create_category(db, "Office", alice)
        _get_or_create_category(db, "Travel", carol)
        _get_or_create_category(db, "Client Entertainment", carol)
        _get_or_create_category(db, "Software/Subscriptions", alice)
        _get_or_create_category(db, "Other", alice)
        db.commit()

        # Sample claims (only on a fresh database) to make the queue + AI flag visible.
        if db.query(ExpenseClaim).count() == 0:
            create_claim(
                db,
                submitter=bob,
                category=office,
                amount="45.00",
                merchant="Staples",
                description="Printer paper and pens for the office.",
                expense_date=date(2026, 8, 20),
                payment_details="Reimburse to IBAN UA00 0000 0000 0000",
            )
            create_claim(
                db,
                submitter=bob,
                category=office,
                amount="820.00",
                merchant="British Airways",
                description="Round-trip flight to London for a client meeting.",
                expense_date=date(2026, 8, 22),
                payment_details="Reimburse to IBAN UA00 0000 0000 0000",
            )

        # Demo reports across the full lifecycle (only on a fresh database).
        if db.query(Report).count() == 0:
            soft = db.query(Category).filter(Category.name == "Software/Subscriptions").first()

            def _rc(cat, amount, merchant, desc, day):
                return create_claim(
                    db, submitter=bob, category=cat, amount=amount, merchant=merchant,
                    description=desc, expense_date=date(2026, 8, day),
                    payment_details="Reimburse to IBAN UA00 0000 0000 0000",
                )

            # Draft (editable)
            create_report(db, bob, "", [_rc(office, "60.00", "IKEA", "Desk lamp for home office.", 24).id,
                                        _rc(soft, "12.00", "Notion", "Notion subscription.", 25).id])
            # Outstanding (submitted -> visible in Alice's queue)
            r_out = create_report(db, bob, "", [_rc(office, "150.00", "Apple", "USB-C hub and cable.", 26).id])
            submit_report(db, r_out, bob)
            # Approved
            r_app = create_report(db, bob, "", [_rc(soft, "99.00", "JetBrains", "IDE annual license.", 27).id])
            submit_report(db, r_app, bob)
            approve_report(db, r_app, alice)
            # Paid
            r_paid = create_report(db, bob, "", [_rc(office, "35.00", "Staples", "Whiteboard markers.", 28).id])
            submit_report(db, r_paid, bob)
            approve_report(db, r_paid, alice)
            mark_paid_report(db, r_paid, alice)

        print("Seed complete. Demo users: alice@ / carol@ / bob@ example.com — password:", DEMO_PASSWORD)
    finally:
        db.close()


if __name__ == "__main__":
    run()
