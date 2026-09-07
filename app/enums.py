"""Canonical enumerations for the expense approval domain."""
from __future__ import annotations

import enum


class ClaimStatus(str, enum.Enum):
    """Life cycle of an expense claim.

    pending    -> awaiting the assigned approver's decision
    approved   -> approver accepted the claim (terminal)
    rejected   -> approver rejected the claim; a comment is mandatory (terminal)
    withdrawn  -> submitter recalled the claim; allowed only while pending (terminal)
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class AiStatus(str, enum.Enum):
    """State of the auxiliary AI analysis attached to a claim.

    pending      -> not generated yet
    ok           -> analysis produced successfully
    unavailable  -> AI could not run (no key / timeout / error). Non-blocking.
    """

    PENDING = "pending"
    OK = "ok"
    UNAVAILABLE = "unavailable"


class AuditAction(str, enum.Enum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ReportStatus(str, enum.Enum):
    """Life cycle of an expense report (Expensify-style)."""

    DRAFT = "draft"
    OUTSTANDING = "outstanding"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"
