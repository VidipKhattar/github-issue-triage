"""Typer CLI entry point for github-issue-triage."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from triage.config import settings
from triage.pipeline import run_pipeline
from triage.reporter import render_json, render_table

app = typer.Typer(
    name="triage",
    help="AI-powered Monday morning triage report for GitHub repositories.",
    add_completion=False,
)
_err = Console(stderr=True)


@app.command()
def run(
    repo_url: Annotated[str, typer.Argument(help="GitHub repo URL or 'owner/repo'.")],
    since: Annotated[
        int | None,
        typer.Option(
            "--since",
            help="Issues from the last N days (default: 7 — one week).",
        ),
    ] = None,
    focus: Annotated[
        str | None,
        typer.Option(
            "--focus",
            "-f",
            help="Maintainer focus directive injected into the LLM prompt, "
            "e.g. 'security and authentication issues'.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Fetch and preprocess but skip the LLM call. "
            "Prints token and cost estimates instead.",
        ),
    ] = False,
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Output format: 'table' (default) or 'json'."),
    ] = "table",
    stale_days: Annotated[
        int,
        typer.Option("--stale-days", "-s", help="Days without update before flagging stale."),
    ] = 90,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            "-p",
            help="LLM provider: 'claude' (default), 'gpt', or 'gemini'. "
            "Set the specific model per provider via MODEL_CLAUDE / "
            "MODEL_OPENAI / MODEL_GEMINI in your .env.",
        ),
    ] = None,
    max_issues: Annotated[
        int,
        typer.Option("--max-issues", "-n", hidden=True),
    ] = settings.max_issues,
) -> None:
    """Fetch open issues from REPO_URL and produce a structured triage report."""
    if output not in ("table", "json"):
        _err.print(f"[red]Error:[/red] --output must be 'table' or 'json', got '{output}'.")
        raise typer.Exit(1)

    since_days = since if since is not None else settings.since_default

    resolved_provider = (provider or settings.default_provider).lower()
    try:
        model = settings.model_for_provider(resolved_provider)
    except ValueError as exc:
        _err.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    try:
        report = run_pipeline(
            repo_url,
            max_issues=max_issues,
            stale_days=stale_days,
            focus=focus,
            since_days=since_days,
            dry_run=dry_run,
            model=model,
        )
    except (OSError, ValueError) as exc:
        _err.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except RuntimeError as exc:
        _err.print(f"[red]Rate limit:[/red] {exc}")
        raise typer.Exit(1) from exc
    except Exception as exc:  # noqa: BLE001
        _err.print(f"[red]Unexpected error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if report is None:
        return

    if output == "json":
        render_json(report)
    else:
        render_table(report)


def main() -> None:
    """Entry point registered by pyproject.toml as the triage script."""
    app()


if __name__ == "__main__":
    main()
