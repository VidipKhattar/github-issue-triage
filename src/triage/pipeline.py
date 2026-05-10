"""Orchestrate the triage pipeline: fetch → preprocess → LLM call → report."""

from __future__ import annotations

import json
import time

import litellm
from rich.console import Console

from triage.config import settings
from triage.github import (
    check_rate_limit,
    fetch_open_issues,
    fetch_repo_stats,
    parse_repo,
)
from triage.llm import run_triage
from triage.models import TriageReport
from triage.preprocessor import enrich_with_comments, preprocess_issues

_console = Console(stderr=True)

# Tunable bounds on cost and rate-limit behaviour.
_COST_WARNING_TOKENS = 50_000  # input tokens above this trigger a warning
_MAX_OUTPUT_TOKENS = (
    8_192  # hard cap on LLM output; also the worst-case used in cost estimation
)
_EST_SYSTEM_TOKENS = (
    300  # measured against the current system prompt; remeasure if it changes
)
_MIN_RATE_LIMIT_REMAINING = 20  # abort below this with a clear message


def _format_count(n: int) -> str:
    """Return a compact string for a non-negative count.

    Args:
        n: The count to format.

    Returns:
        A compact representation: values >= 1,000,000 render as ``"1.5m"``;
        values >= 1,000 as ``"92k"``; smaller values are returned as plain
        digits.
    """
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}m"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _estimate_cost(processed: list, model: str) -> tuple[int, float]:
    """Estimate input tokens and worst-case total cost for an LLM call.

    Uses LiteLLM's model-specific tokenizer for the user payload and assumes
    the call will hit ``_MAX_OUTPUT_TOKENS`` on output, so the returned cost
    is a conservative upper bound rather than an expected value. Both the
    dry-run path and the live cost-warning path call this helper so they
    agree on the same number for the same input.

    Args:
        processed: Preprocessed issues that will be JSON-serialised as the
            user message body.
        model: Resolved LiteLLM model string
            (e.g. ``"claude-sonnet-4-20250514"``).

    Returns:
        A ``(prompt_tokens, total_cost_usd)`` tuple. ``prompt_tokens``
        includes a fixed estimate for the system prompt overhead.
    """
    serialized = json.dumps([p.model_dump() for p in processed])

    prompt_tokens = (
        litellm.token_counter(
            model=model,
            messages=[{"role": "user", "content": serialized}],
        )
        + _EST_SYSTEM_TOKENS
    )
    input_cost, output_cost = litellm.cost_per_token(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=_MAX_OUTPUT_TOKENS,
    )
    return prompt_tokens, input_cost + output_cost


def _print_dry_run(
    repo: str,
    raw_count: int,
    processed: list,
    since_days: int | None,
    model: str,
) -> None:
    """Print a dry-run cost estimate without calling the LLM.

    Uses the same ``_estimate_cost`` helper as the live warning, so the
    quoted figure matches what the live run would produce for the same
    input.

    Args:
        repo: Repository slug formatted as ``"owner/repo"``.
        raw_count: Number of issues fetched before any filtering.
        processed: Issues that would be sent to the LLM after filtering.
        since_days: Value of the ``--since`` flag, or None if unset.
        model: Resolved LiteLLM model string.
    """
    est_tokens, est_cost = _estimate_cost(processed, model)
    filter_label = (
        f"last {since_days} days" if since_days else "all open issues (safety cap)"
    )

    _console.print()
    _console.print("[bold yellow]DRY RUN[/bold yellow]")
    _console.print(f"  Repo:          {repo}")
    _console.print(f"  Filter:        {filter_label}")
    _console.print(f"  Issues found:  {raw_count}")
    _console.print(f"  After filter:  {len(processed)}")
    _console.print(f"  Est. tokens:   ~{est_tokens:,}")
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
    model: str | None = None,
) -> TriageReport | None:
    """End-to-end triage pipeline.

    Args:
        repo_url: Public GitHub repository URL or ``"owner/repo"`` slug.
        max_issues: Safety cap on issues fetched. Hidden from normal usage.
        stale_days: Days without activity before an issue is flagged stale.
        focus: Optional maintainer focus directive forwarded to the LLM prompt.
        since_days: When set, only include issues created within this many days.
        dry_run: When True, skip the LLM call and print a cost estimate instead.
        model: LiteLLM model string. Defaults to ``settings.litellm_model`` when
            not provided.

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
    if rate_info["remaining"] < _MIN_RATE_LIMIT_REMAINING:
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
    raw_issues = fetch_open_issues(
        repo_url, max_issues=max_issues, since_days=since_days
    )
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

    resolved_model = model or settings.litellm_model

    if dry_run:
        _print_dry_run(
            repo_slug, len(raw_issues), processed, since_days, resolved_model
        )
        return None

    prompt_tokens, total_est = _estimate_cost(processed, resolved_model)
    if prompt_tokens > _COST_WARNING_TOKENS:
        _console.print(
            f"[yellow]Warning: large analysis (~{prompt_tokens:,} input tokens, "
            f"est. ${total_est:.2f} total worst-case). "
            f"Consider reducing scope.[/yellow]"
        )

    _console.print(f"[dim]Analysing with {resolved_model}…[/dim]")
    t0 = time.perf_counter()
    report = run_triage(
        repo_slug, processed, focus=focus, repo_stats=repo_stats, model=resolved_model
    )
    elapsed = time.perf_counter() - t0
    _console.print(f"[dim]  ✓ done in {elapsed:.1f}s[/dim]")

    report.total_open_in_repo = total_open
    report.since_days = since_days
    return report
