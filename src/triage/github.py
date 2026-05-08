"""feat: GitHub API client with pagination, rate-limit handling, and optional auth."""

from __future__ import annotations

import re
import time
import warnings
from typing import Any

import httpx
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

from triage.config import settings
from triage.models import RawIssue

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

_API_BASE = "https://api.github.com"
_PAGE_SIZE = 100
_TIMEOUT = 30.0


def parse_repo(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL or 'owner/repo' shorthand.

    Args:
        url: A full GitHub URL (e.g. ``https://github.com/owner/repo``) or
            a short ``owner/repo`` slug.

    Returns:
        A ``(owner, repo)`` tuple of plain strings.

    Raises:
        ValueError: If the URL cannot be parsed into an owner/repo pair.
    """
    url = url.rstrip("/")
    match = re.search(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if match:
        return match.group(1), match.group(2)
    parts = url.split("/")
    if len(parts) == 2:
        return parts[0], parts[1]
    raise ValueError(
        f"Cannot parse repo from '{url}'. "
        "Expected 'https://github.com/owner/repo' or 'owner/repo'."
    )


def _build_headers() -> dict[str, str]:
    """Build HTTP headers for GitHub API requests.

    Includes the ``Authorization`` header only when ``GITHUB_TOKEN`` is set,
    which raises the rate limit from 60 to 5 000 requests per hour.

    Returns:
        A dict of headers suitable for passing to an ``httpx`` client.
    """
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def _check_rate_limit(response: httpx.Response) -> None:
    """Raise if the GitHub rate limit is exhausted.

    Reads ``X-RateLimit-Remaining`` from the response headers and raises
    a ``RuntimeError`` with the seconds-until-reset included in the message
    so the caller can surface a human-readable wait time.

    Args:
        response: A completed ``httpx`` response from the GitHub API.

    Raises:
        RuntimeError: When the remaining rate-limit quota has reached zero.
    """
    remaining = int(response.headers.get("X-RateLimit-Remaining", 1))
    if remaining == 0:
        reset_ts = int(response.headers.get("X-RateLimit-Reset", time.time() + 60))
        wait = max(0, reset_ts - int(time.time())) + 2
        raise RuntimeError(
            f"GitHub rate limit exhausted. Resets in {wait}s. "
            "Set GITHUB_TOKEN in .env to raise the limit to 5 000 req/hr."
        )


def check_rate_limit() -> dict[str, int]:
    """Check the current GitHub API rate limit status.

    Returns:
        Dict with ``remaining``, ``limit``, and ``reset_in_seconds`` keys.
        On any error, returns conservative defaults (remaining=60, limit=60,
        reset_in_seconds=3600) rather than crashing the caller.
    """
    headers = _build_headers()
    try:
        with httpx.Client(base_url=_API_BASE, headers=headers, timeout=_TIMEOUT) as client:
            response = client.get("/rate_limit")
            response.raise_for_status()
            core = response.json()["resources"]["core"]
            return {
                "remaining": int(core["remaining"]),
                "limit": int(core["limit"]),
                "reset_in_seconds": max(0, int(core["reset"]) - int(time.time())),
            }
    except Exception:  # noqa: BLE001
        return {"remaining": 60, "limit": 60, "reset_in_seconds": 3600}


def fetch_repo_stats(owner: str, repo: str) -> dict[str, Any]:
    """Fetch summary stats for a repository.

    Returns:
        Dict with ``stars``, ``forks``, ``topics``, and ``open_issues_count``.
        Returns an empty dict on any error so the caller never crashes.
    """
    headers = _build_headers()
    try:
        with httpx.Client(base_url=_API_BASE, headers=headers, timeout=_TIMEOUT) as client:
            response = client.get(f"/repos/{owner}/{repo}")
            response.raise_for_status()
            data = response.json()
            return {
                "stars": int(data.get("stargazers_count", 0)),
                "forks": int(data.get("forks_count", 0)),
                "topics": list(data.get("topics", [])),
                "open_issues_count": int(data.get("open_issues_count", 0)),
            }
    except Exception:  # noqa: BLE001
        return {}


def _clean_comment(text: str) -> str:
    """Strip HTML, fenced code blocks, inline code, and truncate to 200 chars."""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`]+`", " ", text)
    plain = BeautifulSoup(text, "html.parser").get_text(separator=" ")
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:200] + "…" if len(plain) > 200 else plain


def fetch_top_comments(
    client: httpx.Client,
    owner: str,
    repo: str,
    issue_number: int,
    limit: int = 3,
) -> list[str]:
    """Fetch the top N comments for an issue, cleaned and truncated.

    Args:
        client: An open httpx.Client (caller is responsible for lifecycle).
        owner: Repo owner.
        repo: Repo name.
        issue_number: GitHub issue number.
        limit: Maximum number of comments to return.

    Returns:
        List of cleaned, truncated comment strings. Empty list on any error.
    """
    try:
        response = client.get(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            params={"per_page": limit},
        )
        response.raise_for_status()
        return [_clean_comment(c.get("body", "") or "") for c in response.json()][:limit]
    except Exception:  # noqa: BLE001
        return []


def fetch_repo_open_issue_count(repo_url: str) -> int:
    """Fetch the total open-issue count for a repository.

    Uses the repo endpoint's ``open_issues_count`` field. Note that GitHub
    includes pull requests in this figure, so the returned value is an upper
    bound on the true issue count. Returns 0 on any non-fatal error so the
    caller can degrade gracefully rather than crashing.

    Args:
        repo_url: Full GitHub URL or 'owner/repo' shorthand.

    Returns:
        Total open issues + PRs as reported by GitHub, or 0 if the request
        fails.
    """
    owner, repo = parse_repo(repo_url)
    headers = _build_headers()
    try:
        with httpx.Client(base_url=_API_BASE, headers=headers, timeout=_TIMEOUT) as client:
            response = client.get(f"/repos/{owner}/{repo}")
            response.raise_for_status()
            return int(response.json().get("open_issues_count", 0))
    except Exception:  # noqa: BLE001
        return 0


def fetch_open_issues(repo_url: str, max_issues: int = 100) -> list[RawIssue]:
    """Fetch open issues (not PRs) from a public GitHub repo.

    Always requests full pages of ``_PAGE_SIZE`` items so that pages containing
    many PRs do not cause the loop to exit early. Stops once ``max_issues``
    non-PR issues have been collected or the API returns a partial page.

    Args:
        repo_url: Full GitHub URL or 'owner/repo' shorthand.
        max_issues: Hard cap on returned issues.

    Returns:
        List of RawIssue objects, newest first.

    Raises:
        ValueError: If the URL cannot be parsed or the repo is not found.
        RuntimeError: If the GitHub rate limit is exceeded.
        httpx.HTTPStatusError: For other HTTP errors.
    """
    owner, repo = parse_repo(repo_url)
    headers = _build_headers()
    issues: list[RawIssue] = []
    page = 1

    with httpx.Client(base_url=_API_BASE, headers=headers, timeout=_TIMEOUT) as client:
        while len(issues) < max_issues:
            response = client.get(
                f"/repos/{owner}/{repo}/issues",
                params={
                    "state": "open",
                    "per_page": _PAGE_SIZE,
                    "page": page,
                    "sort": "created",
                    "direction": "desc",
                },
            )
            if response.status_code == 404:
                raise ValueError(f"Repository '{owner}/{repo}' not found or is private.")
            if response.status_code == 403:
                _check_rate_limit(response)
                response.raise_for_status()
            response.raise_for_status()
            _check_rate_limit(response)

            batch: list[dict[str, Any]] = response.json()
            if not batch:
                break

            for item in batch:
                if len(issues) >= max_issues:
                    break
                # GitHub returns PRs in the issues endpoint; skip them
                if item.get("pull_request"):
                    continue
                issues.append(_parse_raw_issue(item))

            if len(batch) < _PAGE_SIZE:
                break
            page += 1

    return issues


def _parse_raw_issue(item: dict[str, Any]) -> RawIssue:
    """Map a raw GitHub API issue dict to a ``RawIssue`` model.

    Args:
        item: A single issue object as returned by the GitHub REST API.

    Returns:
        A validated ``RawIssue`` instance.
    """
    reactions = item.get("reactions", {})
    total_reactions = (
        reactions.get("total_count", 0)
        if isinstance(reactions, dict)
        else 0
    )
    labels = [
        lbl["name"] if isinstance(lbl, dict) else str(lbl)
        for lbl in item.get("labels", [])
    ]
    milestone_title = (
        item["milestone"]["title"]
        if isinstance(item.get("milestone"), dict)
        else None
    )
    return RawIssue(
        number=item["number"],
        title=item["title"],
        body=item.get("body"),
        state=item["state"],
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        comments=item.get("comments", 0),
        reactions_total=total_reactions,
        labels=labels,
        html_url=item["html_url"],
        is_assigned=len(item.get("assignees") or []) > 0,
        author_association=item.get("author_association", "NONE"),
        milestone=milestone_title,
    )
