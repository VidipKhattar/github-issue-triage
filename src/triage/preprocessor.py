"""Issue cleaning and signal extraction before LLM ingestion."""

from __future__ import annotations

import re
import warnings
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

from triage.github import _build_headers, fetch_top_comments
from triage.models import ProcessedIssue, RawIssue

_SNIPPET_CHARS = 500
_COMMENT_TRUNCATE = 200
_API_BASE = "https://api.github.com"
_TIMEOUT = 30.0
_GOOD_FIRST_ISSUE_LABELS = frozenset(
    {"good first issue", "good-first-issue", "beginner", "easy", "starter"}
)
_REPORTER_TYPE_MAP: dict[str, str] = {
    "OWNER": "maintainer",
    "MEMBER": "maintainer",
    "COLLABORATOR": "contributor",
    "CONTRIBUTOR": "contributor",
}


def clean_text(text: str, truncate: int = 0) -> str:
    """Strip HTML, fenced code blocks, and inline code, then collapse whitespace.

    Args:
        text: Raw Markdown/HTML text from a GitHub issue body or comment.
        truncate: If > 0, cap output at this many characters and append ``…``.
            Pass ``0`` (default) for no truncation.

    Returns:
        Plain text with all markup removed and whitespace collapsed.
    """
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`]+`", " ", text)
    plain = BeautifulSoup(text, "html.parser").get_text(separator=" ")
    plain = re.sub(r"\s+", " ", plain).strip()
    if 0 < truncate < len(plain):
        return plain[:truncate] + "…"
    return plain


def _days_since(iso_timestamp: str) -> int:
    """Return whole days elapsed since an ISO-8601 UTC timestamp.

    Args:
        iso_timestamp: A UTC timestamp string in ISO-8601 format, e.g.
            ``"2024-01-15T10:30:00Z"``.

    Returns:
        Number of whole days between the timestamp and now, clamped to
        a minimum of 0 to guard against clock skew.
    """
    dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    delta = datetime.now(tz=timezone.utc) - dt
    return max(0, delta.days)


def preprocess_issues(
    issues: list[RawIssue],
    stale_days: int = 90,
    max_issues: int = 50,
    since_days: int | None = None,
) -> list[ProcessedIssue]:
    """Clean, filter, and cap issues before sending to the LLM.

    Filtering order:
        1. Cap at *max_issues* to bound LLM cost.
        2. Apply *since_days* recency filter when provided.
        3. Clean text and extract numeric signals.

    Args:
        issues: Raw issues from the GitHub API.
        stale_days: Issues with no update for this many days are flagged stale.
        max_issues: Hard cap on output list length.
        since_days: When set, only keep issues created within this many days.
            ``None`` disables the filter (default behaviour).

    Returns:
        List of ProcessedIssue ready for LLM ingestion.
    """
    processed: list[ProcessedIssue] = []

    for issue in issues[:max_issues]:
        raw_body = issue.body or ""
        cleaned_snippet = clean_text(raw_body, _SNIPPET_CHARS)
        days_old = _days_since(issue.created_at)
        days_since_update = _days_since(issue.updated_at)

        if since_days is not None and days_old > since_days:
            continue

        lower_labels = {lbl.lower() for lbl in issue.labels}

        processed.append(
            ProcessedIssue(
                number=issue.number,
                title=issue.title,
                body_snippet=cleaned_snippet,
                created_at=issue.created_at,
                days_old=days_old,
                days_since_update=days_since_update,
                comment_count=issue.comments,
                reaction_count=issue.reactions_total,
                labels=issue.labels,
                html_url=issue.html_url,
                is_stale=days_since_update >= stale_days,
                has_good_first_issue_label=bool(
                    lower_labels & _GOOD_FIRST_ISSUE_LABELS
                ),
                is_assigned=issue.is_assigned,
                reporter_type=_REPORTER_TYPE_MAP.get(
                    issue.author_association, "community"
                ),
                milestone=issue.milestone,
            )
        )

    return processed


def enrich_with_comments(
    processed: list[ProcessedIssue],
    owner: str,
    repo: str,
) -> int:
    """Fetch top comments for each issue with comment_count > 0 and attach in place.

    Uses a single shared httpx.Client for all requests rather than opening a
    new connection per issue.

    Args:
        processed: Processed issues to enrich. Mutated in place.
        owner: Repo owner.
        repo: Repo name.

    Returns:
        Count of issues for which comments were successfully fetched.
    """
    fetched = 0
    headers = _build_headers()
    with httpx.Client(base_url=_API_BASE, headers=headers, timeout=_TIMEOUT) as client:
        for issue in processed:
            if issue.comment_count <= 0:
                continue
            raw_comments = fetch_top_comments(client, owner, repo, issue.number)
            comments = [clean_text(c, truncate=_COMMENT_TRUNCATE) for c in raw_comments]
            if comments:
                issue.top_comments = comments
                fetched += 1
    return fetched


def issues_to_llm_payload(issues: list[ProcessedIssue]) -> list[dict]:
    """Serialise processed issues to a compact dict list for prompt injection.

    Excludes fields that are redundant in the prompt context (e.g. ``html_url``)
    to keep token usage low.

    Args:
        issues: Preprocessed issues to serialise.

    Returns:
        A list of plain dicts, one per issue, ready to embed in the LLM prompt
        via ``json.dumps``.
    """
    return [
        {
            "number": i.number,
            "title": i.title,
            "body": i.body_snippet,
            "days_old": i.days_old,
            "days_since_update": i.days_since_update,
            "comments": i.comment_count,
            "reactions": i.reaction_count,
            "labels": i.labels,
            "is_stale": i.is_stale,
            "good_first_issue": i.has_good_first_issue_label,
            "is_assigned": i.is_assigned,
            "reporter_type": i.reporter_type,
            "milestone": i.milestone,
            "top_comments": i.top_comments,
        }
        for i in issues
    ]
