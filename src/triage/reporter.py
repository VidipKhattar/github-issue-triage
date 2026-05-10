"""Formats TriageReport into Rich CLI output or JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from triage.config import settings
from triage.models import TriageReport

_PRIORITY_COLORS: dict[str, str] = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "dim",
}

console = Console()

_AI_MARKER = " [dim]*[/dim]"
_LEGEND = "[dim]  * AI generated  ·  all other columns are GitHub data[/dim]"


def _format_age(created_at: str) -> str:
    """Format a creation timestamp as a human-readable age string.

    Args:
        created_at: ISO-8601 UTC timestamp string, e.g. ``"2024-01-15T10:30:00Z"``.

    Returns:
        A compact age string: ``"30s ago"``, ``"45m ago"``, ``"6h ago"``,
        ``"12d ago"``, or ``"—"`` if the timestamp is missing.
    """
    if not created_at:
        return "—"
    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    seconds = int((datetime.now(tz=timezone.utc) - dt).total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3_600:
        return f"{seconds // 60}m ago"
    if seconds < 86_400:
        return f"{seconds // 3_600}h ago"
    return f"{seconds // 86_400}d ago"


def _priority_badge(priority: str) -> Text:
    """Return a coloured Rich Text label for the given priority level.

    Args:
        priority: Priority string — one of critical, high, medium, low.

    Returns:
        A styled Rich Text object.
    """
    color = _PRIORITY_COLORS.get(priority, "white")
    return Text(priority.upper(), style=color)


def _context_line(report: TriageReport) -> str:
    """Build the single-line context string shown at the top of every report."""
    window = f"the last {report.since_days} days" if report.since_days else "all open issues"
    return (
        f"Showing {report.total_issues_analyzed} issues from {window} · "
        f"{report.total_open_in_repo:,} total open issues · {report.repo}"
    )


def render_table(report: TriageReport) -> None:
    """Print a human-readable Rich report to stdout.

    Renders all non-empty sections (priorities, stale issues, quick wins,
    duplicates) as separate Rich tables. Issue clusters are omitted from the
    table view — they are available in JSON output.

    Args:
        report: The completed triage report to display.
    """
    console.print()
    console.print(
        Panel(
            f"[bold cyan]GitHub Issue Triage Report[/bold cyan]\n"
            f"[dim]{_context_line(report)}[/dim]",
            box=box.ROUNDED,
        )
    )

    if report.summary:
        console.print(
            Panel(report.summary, title="[bold]Executive Summary[/bold]", box=box.SIMPLE)
        )

    # --- Top Priorities ---
    if report.top_priorities:
        table = Table(
            title="Top Priorities",
            box=box.SIMPLE_HEAD,
            show_lines=True,
            expand=True,
        )
        table.add_column("#", style="dim", width=6)
        table.add_column("Opened", width=10)
        table.add_column(f"Priority{_AI_MARKER}", width=10)
        table.add_column("GitHub Labels", width=16)
        table.add_column(f"Category{_AI_MARKER}", width=12)
        table.add_column("Title", style="bold")
        table.add_column(f"Reasoning{_AI_MARKER}")
        table.add_column(f"Suggested Action{_AI_MARKER}")
        table.add_column("URL", style="cyan")

        for p in report.top_priorities:
            age = _format_age(p.created_at)
            label_str = ", ".join(p.labels) if p.labels else "—"
            table.add_row(
                str(p.number),
                age,
                _priority_badge(p.priority),
                label_str,
                p.category,
                p.title,
                p.reasoning,
                p.suggested_action,
                p.html_url,
            )
        console.print(table)
        console.print(_LEGEND)

    # --- Stale Issues ---
    if report.stale_issues:
        stale_table = Table(
            title="Stale Issues (consider closing)",
            box=box.SIMPLE_HEAD,
            expand=True,
        )
        stale_table.add_column("#", style="dim", width=6)
        stale_table.add_column("Opened", width=10)
        stale_table.add_column("Title", style="bold")
        stale_table.add_column(f"Reason{_AI_MARKER}")
        stale_table.add_column("URL", style="cyan")

        for s in report.stale_issues:
            stale_table.add_row(
                str(s.number), _format_age(s.created_at), s.title, s.reason, s.html_url
            )
        console.print(stale_table)
        console.print(_LEGEND)

    # --- Quick Wins ---
    if report.quick_wins:
        qw_table = Table(
            title="Quick Wins / Good First Issues",
            box=box.SIMPLE_HEAD,
            expand=True,
        )
        qw_table.add_column("#", style="dim", width=6)
        qw_table.add_column("Opened", width=10)
        qw_table.add_column("Title", style="bold")
        qw_table.add_column(f"Why it's quick{_AI_MARKER}")
        qw_table.add_column("URL", style="cyan")

        priority_numbers = {p.number for p in report.top_priorities}
        for q in report.quick_wins:
            note = (
                "\n[dim italic]Also in Top Priorities ↑[/dim italic]"
                if q.number in priority_numbers
                else ""
            )
            qw_table.add_row(
                str(q.number), _format_age(q.created_at), q.title, q.why_quick + note, q.html_url
            )
        console.print(qw_table)
        console.print(_LEGEND)

    # --- Duplicates ---
    if report.duplicate_groups:
        dup_table = Table(
            title="Likely Duplicates",
            box=box.SIMPLE_HEAD,
            expand=True,
        )
        dup_table.add_column("Canonical #", style="bold", width=12)
        dup_table.add_column("Duplicates")
        dup_table.add_column(f"Reasoning{_AI_MARKER}")

        for d in report.duplicate_groups:
            others = ", ".join(f"#{n}" for n in d.issue_numbers if n != d.canonical_number)
            dup_table.add_row(f"#{d.canonical_number}", others, d.reasoning)
        console.print(dup_table)
        console.print(_LEGEND)

    # --- Category Breakdown ---
    counts: dict[str, int] = {}
    for ic in report.issue_categories:
        if ic.category:
            counts[ic.category] = counts.get(ic.category, 0) + 1
    if counts:
        cat_table = Table(
            title=f"Issue Breakdown by Category{_AI_MARKER}",
            box=box.SIMPLE_HEAD,
            expand=True,
        )
        cat_table.add_column("Category", style="bold", width=20)
        cat_table.add_column("Count", justify="right", width=8)
        for category, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
            cat_table.add_row(category, str(count))
        console.print(cat_table)
        console.print(_LEGEND)

    console.print()


def render_json(report: TriageReport) -> None:
    """Print the report as pretty-printed JSON to stdout.

    Args:
        report: The completed triage report to serialise.
    """
    output = {
        "meta": {
            "repo": report.repo,
            "issues_shown": report.total_issues_analyzed,
            "total_open_issues": report.total_open_in_repo,
            "since_days": report.since_days,
            "model": settings.litellm_model,
        },
        **report.model_dump(),
    }
    print(json.dumps(output, indent=2))
