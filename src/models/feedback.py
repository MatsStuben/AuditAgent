"""Auditor overrides and the candidate methodology rules derived from them (SPEC 18, 19)."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RuleProposalStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class AuditorFeedback(BaseModel):
    """A record of one auditor override (SPEC 18).

    `before` preserves the original system output so it is never lost by the overwrite.
    """

    id: str
    object_type: str
    object_id: str
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    engagement_context: dict[str, Any] = Field(default_factory=dict)


class RuleProposal(BaseModel):
    """A candidate methodology rule inferred from feedback (SPEC 19).

    Always advisory: nothing in the engine applies a proposal automatically, and no static
    config file is ever mutated by this pipeline.
    """

    id: str
    condition: str
    action: str
    reason: str
    source_feedback_id: str
    status: RuleProposalStatus = RuleProposalStatus.PENDING_REVIEW
