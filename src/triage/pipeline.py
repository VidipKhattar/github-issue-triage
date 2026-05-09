"""Orchestrates fetch → preprocess → LLM call → report."""

from __future__ import annotations

import time

from rich.console import Console

from triage.config import settings
from triage.github import (
    check_rate_limit,
    fetch_open_issues,
    fetch_repo_stats,
    parse_repo,
)
from triage.llm import _model_pricing, run_triage
from triage.models import TriageReport
from triage.preprocessor import enrich_with_comments, preprocess_issues

_console = Console(stderr=True)

_TOKENS_PER_ISSUE = 180
_EST_OUTPUT_TOKENS = 600
_EST_SYSTEM_TOKENS = 300
_COST_WARNING_TOKENS = 50_000


def _format_count(n: int) -> str:
    """Format a count compactly: 92,000 → '92k', 1,500,000 → '1.5m'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}m"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _print_dry_run(
    repo: str,
    raw_count: int,
    processed_count: int,
    since_days: int | None,
    model: str,
) -> None:
    """Print a dry-run cost estimate without calling the LLM.

    Args:
        repo: Repository slug (owner/repo).
        raw_count: Number of issues fetched before the since filter.
        processed_count: Number of issues that would be sent to the LLM.
        since_days: The --since filter value, or None if not set.
        model: LiteLLM model string.
    """
    est_in = _TOKENS_PER_ISSUE * processed_count + _EST_SYSTEM_TOKENS
    est_out = _EST_OUTPUT_TOKENS
    pricing = _model_pricing(model)
    est_cost = (est_in * pricing["input"] + est_out * pricing["output"]) / 1_000_000
    filter_label = f"last {since_days} days" if since_days else "all open issues (safety cap)"

    _console.print()
    _console.print("[bold yellow]DRY RUN[/bold yellow]")
    _console.print(f"  Repo:          {repo}")
    _console.print(f"  Filter:        {filter_label}")
    _console.print(f"  Issues found:  {raw_count}")
    _console.print(f"  After filter:  {processed_count}")
    _console.print(f"  Est. tokens:   ~{est_in:,}")
    _console.print(f"  Est. cost:     ~${est_cost:.4f}")
    _console.print(f"  Model:         {model}")
    _console.print()


def run_pipeline(
    repo_url: str,
    max_issues: int = 500,
    stale_days: int = 90,
    focus: str | None = None,
    since_days: int | None = None,
    dry_run: bool = False,
) -> TriageReport | None:
    """End-to-end triage pipeline.

    Args:
        repo_url: Public GitHub repository URL or ``"owner/repo"`` slug.
        max_issues: Safety cap on issues fetched. Hidden from normal usage.
        stale_days: Days without activity before an issue is flagged stale.
        focus: Optional maintainer focus directive forwarded to the LLM prompt.
        since_days: When set, only include issues created within this many days.
        dry_run: When True, skip the LLM call and print a cost estimate instead.

    Returns:
        Validated TriageReport, or ``None`` when *dry_run* is True.

    Raises:
        ValueError: Malformed repo URL or LLM JSON.
        OSError: Missing API key.
        RuntimeError: GitHub rate limit exceeded.
    """
    owner, repo = parse_repo(repo_url)
    repo_slug = f"{owner}/{repo}"

    rate_info = check_rate_limit()
    if rate_info["remaining"] < 20:
        mins = max(1, rate_info["reset_in_seconds"] // 60)
        raise RuntimeError(
            f"GitHub rate limit nearly exhausted ({rate_info['remaining']} remaining, "
            f"resets in {mins} minutes). "
            "Set GITHUB_TOKEN in your .env for 5000 requests/hour."
        )

    repo_stats = fetch_repo_stats(owner, repo)
    stars_suffix = (
        f" [dim]({_format_count(repo_stats['stars'])} stars)[/dim]"
        if repo_stats.get("stars")
        else ""
    )

    _console.print(f"[dim]Fetching issues from {repo_slug}…[/dim]", end=" ")
    total_open = repo_stats.get("open_issues_count", 0)
    raw_issues = fetch_open_issues(repo_url, max_issues=max_issues, since_days=since_days)
    _console.print(f"[green]✓[/green] {len(raw_issues)} found{stars_suffix}")

    if not raw_issues:
        return TriageReport(
            repo=repo_slug,
            total_open_in_repo=total_open,
            total_issues_analyzed=0,
            since_days=since_days,
            summary="No open issues found in this repository.",
        )

    _console.print("[dim]Filtering and preprocessing…[/dim]", end=" ")
    processed = preprocess_issues(
        raw_issues,
        stale_days=stale_days,
        max_issues=max_issues,
        since_days=since_days,
    )
    cap_applied = len(raw_issues) >= max_issues and since_days is None
    filtered_out = len(raw_issues) - len(processed)

    if since_days is not None:
        msg = (
            f"[green]✓[/green] {len(processed)} issues selected "
            f"from last {since_days} days"
        )
        if filtered_out:
            msg += f" [dim]({filtered_out} filtered out)[/dim]"
    else:
        msg = f"[green]✓[/green] {len(processed)} issues selected"
        if cap_applied:
            msg += " [dim](safety cap applied)[/dim]"
    _console.print(msg)

    if not processed:
        return TriageReport(
            repo=repo_slug,
            total_open_in_repo=total_open,
            total_issues_analyzed=0,
            since_days=since_days,
            summary="No issues matched the current filters.",
        )

    if not dry_run:
        issues_with_comments = sum(1 for p in processed if p.comment_count > 0)
        if issues_with_comments:
            fresh_rate = check_rate_limit()
            needed = issues_with_comments * 2
            if fresh_rate["remaining"] < needed:
                _console.print(
                    f"[yellow]Skipping comment enrichment — low GitHub API quota "
                    f"({fresh_rate['remaining']} remaining). "
                    "Add GITHUB_TOKEN to .env for 5000 requests/hour.[/yellow]"
                )
            else:
                _console.print("[dim]Enriching with comments…[/dim]", end=" ")
                fetched = enrich_with_comments(processed, owner, repo)
                _console.print(
                    f"[green]✓[/green] {fetched} of {issues_with_comments} fetched"
                )

    model = settings.litellm_model

    if dry_run:
        _print_dry_run(repo_slug, len(raw_issues), len(processed), since_days, model)
        return None

    est_tokens = len(processed) * _TOKENS_PER_ISSUE + _EST_SYSTEM_TOKENS
    if est_tokens > _COST_WARNING_TOKENS:
        pricing = _model_pricing(model)
        if since_days and since_days > 1:
            suggestion = f"try --since {max(1, since_days // 2)} to halve the window"
        else:
            suggestion = "try --max-issues 100 to cap the issue count"
        _console.print(
            f"[yellow]Warning: large analysis (~{est_tokens:,} tokens, est. "
            f"${est_tokens * pricing['input'] / 1_000_000:.2f}+). "
            f"Consider reducing scope — {suggestion}.[/yellow]"
        )

    _console.print(f"[dim]Analysing with {model}…[/dim]")
    t0 = time.time()
    report = run_triage(repo_slug, processed, focus=focus, repo_stats=repo_stats)
    elapsed = time.time() - t0
    _console.print(f"[dim]  ✓ done in {elapsed:.1f}s[/dim]")

    report.total_open_in_repo = total_open
    report.since_days = since_days
    return report
