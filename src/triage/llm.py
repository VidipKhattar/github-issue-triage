"""LiteLLM wrapper returning validated TriageReport output."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ["LITELLM_TELEMETRY"] = "False"

import litellm  # noqa: E402
from rich.console import Console

from triage.config import settings
from triage.models import ProcessedIssue, TriageReport
from triage.preprocessor import issues_to_llm_payload

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_console = Console(stderr=True)

# Approximate pricing per million tokens (input, output) keyed by model prefix.
_PRICING: dict[str, dict[str, float]] = {
    "claude": {"input": 3.00, "output": 15.00},
    "gpt": {"input": 2.50, "output": 10.00},
    "gemini": {"input": 1.25, "output": 5.00},
}


def _model_pricing(model: str) -> dict[str, float]:
    """Return pricing dict for *model* by matching against known prefixes."""
    for prefix, pricing in _PRICING.items():
        if model.startswith(prefix):
            return pricing
    return {"input": 3.00, "output": 15.00}


def _load_system_prompt(schema: dict, focus: str | None = None) -> str:
    """Load the system prompt template from disk and inject the schema.

    Args:
        schema: JSON schema dict to embed in the prompt.
        focus: Optional maintainer focus directive appended after the main prompt.

    Returns:
        Fully rendered system prompt string.
    """
    template = (_PROMPTS_DIR / "system.md").read_text()
    prompt = template.format(schema=json.dumps(schema, indent=2))
    if focus:
        focus_template = (_PROMPTS_DIR / "focus.md").read_text()
        prompt += "\n\n" + focus_template.format(focus=focus)
    return prompt


def _build_user_prompt(
    repo: str,
    issues: list[ProcessedIssue],
    repo_stats: dict | None = None,
) -> str:
    """Build the user-turn message sent to the LLM.

    Args:
        repo: Full repository identifier (e.g. ``"owner/repo"``).
        issues: Preprocessed issues to include in the prompt.
        repo_stats: Optional dict with ``stars``, ``forks``, ``topics`` to seed
            community-signal context for the LLM.

    Returns:
        Formatted string containing the repo name, optional stats line, and a
        JSON array of issues.
    """
    payload = issues_to_llm_payload(issues)
    header = f"Repository: {repo}"
    if repo_stats:
        stars = repo_stats.get("stars", 0)
        forks = repo_stats.get("forks", 0)
        topics = repo_stats.get("topics", [])
        line = f"\nStars: {stars:,} | Forks: {forks:,}"
        if topics:
            line += f" | Topics: {', '.join(topics)}"
        header += line
    return (
        f"{header}\n\n"
        f"Open issues ({len(issues)} total):\n"
        f"{json.dumps(payload, indent=2)}"
    )


def complete(model: str, system: str, user: str) -> tuple[str, object]:
    """Call LLM via LiteLLM and return response text and token usage.

    Args:
        model: LiteLLM model string e.g. ``"claude-sonnet-4-20250514"`` or ``"gpt-4o"``.
        system: System prompt text.
        user: User message text.

    Returns:
        Tuple of ``(response_text, usage)`` where ``usage`` has
        ``prompt_tokens`` and ``completion_tokens`` attributes.

    Raises:
        litellm.exceptions.AuthenticationError: If the API key is invalid.
        litellm.exceptions.RateLimitError: If the rate limit is exceeded.
        ValueError: If the response cannot be parsed.
    """
    response = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=8192,
        num_retries=3,
    )
    return response.choices[0].message.content, response.usage


def _parse_json_from_text(text: str) -> dict:
    """Extract the first JSON object from text that may have surrounding prose.

    Args:
        text: Raw LLM response text.

    Returns:
        The parsed JSON object as a dict.

    Raises:
        ValueError: If no JSON object delimiters are found or the JSON is invalid.
    """
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("LLM response contained no JSON object.")
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM response was not valid JSON (likely truncated): {exc}. "
            "Try reducing --max-issues or using --since to narrow the window."
        ) from exc


def run_triage(
    repo: str,
    issues: list[ProcessedIssue],
    focus: str | None = None,
    repo_stats: dict | None = None,
) -> TriageReport:
    """Call the LLM and return a validated TriageReport.

    Args:
        repo: Full repo identifier (e.g. ``"owner/repo"``).
        issues: Preprocessed issues to analyse.
        focus: Optional maintainer focus directive injected into the system prompt.
        repo_stats: Optional repo stats (stars, forks, topics) included as a
            context header in the user prompt.

    Returns:
        Validated TriageReport instance with html_url fields backfilled.

    Raises:
        ValueError: If the LLM returns malformed JSON.
        pydantic.ValidationError: If the JSON does not match the schema.
    """
    model = settings.litellm_model
    schema = TriageReport.model_json_schema()
    schema.get("properties", {}).pop("total_open_in_repo", None)
    system = _load_system_prompt(schema, focus)
    user = _build_user_prompt(repo, issues, repo_stats)

    text, usage = complete(model, system, user)

    pricing = _model_pricing(model)
    cost = (
        usage.prompt_tokens * pricing["input"]
        + usage.completion_tokens * pricing["output"]
    ) / 1_000_000
    _console.print(
        f"[dim]Tokens: {usage.prompt_tokens:,} in · "
        f"{usage.completion_tokens:,} out · est. cost ${cost:.4f}[/dim]"
    )

    data = _parse_json_from_text(text)
    data.setdefault("repo", repo)
    data.setdefault("total_issues_analyzed", len(issues))

    url_map = {i.number: i.html_url for i in issues}
    created_at_map = {i.number: i.created_at for i in issues}
    labels_map = {i.number: i.labels for i in issues}
    report = TriageReport.model_validate(data)
    _backfill_issue_meta(report, url_map, created_at_map, labels_map)
    return report


def _backfill_issue_meta(
    report: TriageReport,
    url_map: dict[int, str],
    created_at_map: dict[int, str],
    labels_map: dict[int, list[str]],
) -> None:
    """Fill html_url, created_at, and labels from preprocessed issue data.

    Args:
        report: The validated TriageReport to mutate in place.
        url_map: Mapping of issue number to its GitHub HTML URL.
        created_at_map: Mapping of issue number to its ISO-8601 creation timestamp.
        labels_map: Mapping of issue number to its GitHub label list.
    """
    for p in report.top_priorities:
        if not p.html_url:
            p.html_url = url_map.get(p.number, "")
        p.created_at = created_at_map.get(p.number, "")
        p.labels = labels_map.get(p.number, [])
    for s in report.stale_issues:
        if not s.html_url:
            s.html_url = url_map.get(s.number, "")
        s.created_at = created_at_map.get(s.number, "")
    for q in report.quick_wins:
        if not q.html_url:
            q.html_url = url_map.get(q.number, "")
        q.created_at = created_at_map.get(q.number, "")
