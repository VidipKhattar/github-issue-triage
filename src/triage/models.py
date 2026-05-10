"""Pydantic models for structured LLM output and internal issue representation."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PriorityLevel(str, Enum):
    """Priority levels used for reporter colour mapping."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RawIssue(BaseModel):
    """Minimal GitHub API representation of an issue.

    Attributes:
        number: GitHub issue number.
        title: Issue title.
        body: Raw issue body, may be None if the author left it blank.
        state: Issue state — always ``"open"`` in this pipeline.
        created_at: ISO-8601 UTC timestamp of creation.
        updated_at: ISO-8601 UTC timestamp of last update.
        comments: Number of comments on the issue.
        reactions_total: Total reaction count across all emoji types.
        labels: List of label name strings.
        html_url: GitHub web URL for the issue.
    """

    number: int
    title: str
    body: str | None
    state: str
    created_at: str
    updated_at: str
    comments: int
    reactions_total: int = 0
    labels: list[str] = Field(default_factory=list)
    html_url: str
    is_assigned: bool = False
    author_association: str = "NONE"
    milestone: str | None = None


class ProcessedIssue(BaseModel):
    """Cleaned issue ready to be sent to the LLM.

    Attributes:
        number: GitHub issue number.
        title: Issue title.
        body_snippet: Truncated, HTML-stripped body text.
        days_old: Days elapsed since the issue was created.
        days_since_update: Days elapsed since the last update.
        comment_count: Number of comments.
        reaction_count: Total reaction count.
        labels: List of label name strings.
        html_url: GitHub web URL for the issue.
        is_stale: True if days_since_update exceeds the stale threshold.
        has_good_first_issue_label: True if any label matches known beginner labels.
    """

    number: int
    title: str
    body_snippet: str
    created_at: str
    days_old: int
    days_since_update: int
    comment_count: int
    reaction_count: int
    labels: list[str]
    html_url: str
    is_stale: bool
    has_good_first_issue_label: bool
    top_comments: list[str] = Field(default_factory=list)
    is_assigned: bool = False
    reporter_type: str = "community"
    milestone: str | None = None


class IssuePriority(BaseModel):
    """A single issue surfaced by the LLM as a top priority.

    Attributes:
        number: GitHub issue number.
        title: Issue title.
        priority: Priority level — one of critical, high, medium, low.
        confidence: LLM confidence in this priority rating (0.0–1.0).
        reasoning: Why this issue deserves its priority level.
        category: Thematic category, e.g. bug, security, performance, docs.
        suggested_action: Specific next step the maintainer should take.
        html_url: GitHub web URL, backfilled by the pipeline after LLM call.
    """

    number: int
    title: str
    priority: Literal["critical", "high", "medium", "low"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    category: str
    suggested_action: str
    html_url: str = ""
    created_at: str = ""
    labels: list[str] = Field(default_factory=list)


class IssueCluster(BaseModel):
    """A thematic cluster of related issues.

    Attributes:
        theme: Short label for the cluster theme.
        issue_numbers: Issue numbers belonging to this cluster.
        summary: One-sentence description of the common thread.
    """

    theme: str
    issue_numbers: list[int]
    summary: str


class StaleIssue(BaseModel):
    """An issue the LLM recommends closing or following up on.

    Attributes:
        number: GitHub issue number.
        title: Issue title.
        reason: Why this issue should be closed or pinged.
        html_url: GitHub web URL, backfilled by the pipeline after LLM call.
        created_at: ISO-8601 creation timestamp, backfilled by the pipeline.
    """

    number: int
    title: str
    reason: str = ""
    category: str = ""
    html_url: str = ""
    created_at: str = ""

    @model_validator(mode="before")
    @classmethod
    def _remap_reason(cls, data: object) -> object:
        if isinstance(data, dict) and not data.get("reason"):
            for alt in ("why", "description", "explanation", "rationale"):
                if data.get(alt):
                    data["reason"] = data[alt]
                    break
        return data


class QuickWin(BaseModel):
    """An issue suitable for a new contributor or a small fix.

    Attributes:
        number: GitHub issue number.
        title: Issue title.
        why_quick: Explanation of why this is a tractable issue.
        html_url: GitHub web URL, backfilled by the pipeline after LLM call.
        created_at: ISO-8601 creation timestamp, backfilled by the pipeline.
    """

    number: int
    title: str
    why_quick: str = ""
    category: str = ""
    html_url: str = ""
    created_at: str = ""

    @model_validator(mode="before")
    @classmethod
    def _remap_why_quick(cls, data: object) -> object:
        if isinstance(data, dict) and not data.get("why_quick"):
            for alt in ("reason", "description", "why", "explanation", "rationale"):
                if data.get(alt):
                    data["why_quick"] = data[alt]
                    break
        return data


class IssueCategory(BaseModel):
    """Category assignment for a single issue.

    Attributes:
        number: GitHub issue number.
        category: Thematic category — one of bug, security, performance,
            documentation, feature, or other.
    """

    number: int
    category: str


class DuplicateGroup(BaseModel):
    """A set of issues that likely describe the same problem.

    Attributes:
        issue_numbers: All issue numbers in the duplicate group.
        canonical_number: The issue to keep; others can be closed as duplicates.
        reasoning: Why these issues are considered duplicates.
    """

    issue_numbers: list[int]
    canonical_number: int
    reasoning: str


class TriageReport(BaseModel):
    """Top-level structured output from the LLM triage call.

    Attributes:
        repo: Repository identifier in ``owner/repo`` form.
        total_open_in_repo: Total open issues in the repo (set by pipeline).
        total_issues_analyzed: Number of issues sent to the LLM.
        clusters: Thematic issue clusters.
        top_priorities: Up to five highest-priority issues with reasoning.
        stale_issues: Issues recommended for closure or follow-up.
        quick_wins: Issues suitable for new contributors.
        duplicate_groups: Sets of issues describing the same problem.
        summary: Two-to-three sentence executive summary.
    """

    repo: str
    total_open_in_repo: int = 0
    total_issues_analyzed: int
    since_days: int | None = None
    clusters: list[IssueCluster] = Field(default_factory=list)
    top_priorities: list[IssuePriority] = Field(default_factory=list)
    stale_issues: list[StaleIssue] = Field(default_factory=list)
    quick_wins: list[QuickWin] = Field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = Field(default_factory=list)
    issue_categories: list[IssueCategory] = Field(default_factory=list)
    summary: str = ""
