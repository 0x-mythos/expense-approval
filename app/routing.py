"""Claim routing rules.

The single place that decides which approver a claim goes to. MVP rule is
category-based (each category is owned by one approver). Keeping it here means
future rules — amount thresholds, multi-level chains, delegation/backup — can be
added without touching the submit flow.
"""
from app.models import Category, User


def resolve_approver(category: Category) -> User | None:
    return category.approver
