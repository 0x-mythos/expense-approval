"""Auth dependencies. Authorization/scoping is enforced here and in the service
layer — never only in templates."""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """Optional current user (used by pages that render for guests too)."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, user_id)


def require_user(
    user: User | None = Depends(get_current_user),
) -> User:
    """Require a logged-in user; otherwise redirect to the login page."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user


def require_approver(user: User = Depends(require_user)) -> User:
    if not user.is_approver:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Approver role required")
    return user
