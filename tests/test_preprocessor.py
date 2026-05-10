"""tests: unit tests for preprocessing logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from triage.models import RawIssue
from triage.preprocessor import (
    _days_since,
    clean_text,
    issues_to_llm_payload,
    preprocess_issues,
)


def _make_issue(
    number: int = 1,
    title: str = "Test issue",
    body: str | None = "Some body text",
    days_old: int = 10,
    days_since_update: int = 5,
    comments: int = 0,
    reactions: int = 0,
    labels: list[str] | None = None,
) -> RawIssue:
    now = datetime.now(tz=timezone.utc)
    created_at = (now - timedelta(days=days_old)).isoformat().replace("+00:00", "Z")
    updated_at = (now - timedelta(days=days_since_update)).isoformat().replace("+00:00", "Z")
    return RawIssue(
        number=number,
        title=title,
        body=body,
        state="open",
        created_at=created_at,
        updated_at=updated_at,
        comments=comments,
        reactions_total=reactions,
        labels=labels or [],
        html_url=f"https://github.com/owner/repo/issues/{number}",
    )


class TestCleanText:
    def test_strips_html_tags(self):
        result = clean_text("<p>Hello <b>world</b></p>")
        assert "<p>" not in result
        assert "Hello" in result
        assert "world" in result

    def test_strips_fenced_code_blocks(self):
        text = "Before\n```python\nprint('hello')\n```\nAfter"
        result = clean_text(text)
        assert "print" not in result
        assert "Before" in result
        assert "After" in result

    def test_strips_inline_code(self):
        result = clean_text("Call `my_function()` to do it")
        assert "my_function" not in result
        assert "Call" in result

    def test_collapses_whitespace(self):
        result = clean_text("Hello    \n\n   world")
        assert "  " not in result

    def test_empty_string(self):
        assert clean_text("") == ""

    def test_plain_text_unchanged(self):
        result = clean_text("Plain text with no markup.")
        assert result == "Plain text with no markup."

    def test_truncates_with_ellipsis(self):
        result = clean_text("Hello world", truncate=5)
        assert result == "Hello…"

    def test_no_truncation_when_short(self):
        result = clean_text("Hi", truncate=10)
        assert result == "Hi"

    def test_no_truncation_when_zero(self):
        result = clean_text("Hello world", truncate=0)
        assert result == "Hello world"


class TestDaysSince:
    def test_recent_timestamp_returns_zero(self):
        ts = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
        assert _days_since(ts) == 0

    def test_old_timestamp_returns_correct_days(self):
        ts = (datetime.now(tz=timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        assert _days_since(ts) == 30

    def test_never_returns_negative(self):
        future_dt = datetime.now(tz=timezone.utc) + timedelta(days=1)
        future = future_dt.isoformat().replace("+00:00", "Z")
        assert _days_since(future) == 0


class TestPreprocessIssues:
    def test_basic_processing(self):
        issues = [_make_issue(1)]
        result = preprocess_issues(issues)
        assert len(result) == 1
        assert result[0].number == 1

    def test_caps_at_max_issues(self):
        issues = [_make_issue(i) for i in range(1, 11)]
        result = preprocess_issues(issues, max_issues=5)
        assert len(result) == 5

    def test_stale_flag_set_correctly(self):
        stale = _make_issue(1, days_since_update=100)
        fresh = _make_issue(2, days_since_update=10)
        result = preprocess_issues([stale, fresh], stale_days=90)
        stale_result = next(r for r in result if r.number == 1)
        fresh_result = next(r for r in result if r.number == 2)
        assert stale_result.is_stale is True
        assert fresh_result.is_stale is False

    def test_good_first_issue_label_detected(self):
        issue = _make_issue(1, labels=["good first issue"])
        result = preprocess_issues([issue])
        assert result[0].has_good_first_issue_label is True

    def test_good_first_issue_label_variants(self):
        for label in ["good-first-issue", "beginner", "easy", "starter"]:
            issue = _make_issue(1, labels=[label])
            result = preprocess_issues([issue])
            assert result[0].has_good_first_issue_label is True, f"Failed for label: {label}"

    def test_body_truncated_to_500_chars(self):
        long_body = "x" * 1000
        issue = _make_issue(1, body=long_body)
        result = preprocess_issues([issue])
        assert len(result[0].body_snippet) <= 504  # 500 + "…" could add a few bytes

    def test_none_body_handled(self):
        issue = _make_issue(1, body=None)
        result = preprocess_issues([issue])
        assert result[0].body_snippet == ""

    def test_html_stripped_from_body(self):
        issue = _make_issue(1, body="<p>Fix the <b>bug</b></p>")
        result = preprocess_issues([issue])
        assert "<p>" not in result[0].body_snippet
        assert "bug" in result[0].body_snippet

    def test_reaction_and_comment_counts_preserved(self):
        issue = _make_issue(1, comments=5, reactions=10)
        result = preprocess_issues([issue])
        assert result[0].comment_count == 5
        assert result[0].reaction_count == 10

    def test_empty_list_returns_empty(self):
        assert not preprocess_issues([])


class TestIssuesToLlmPayload:
    def test_returns_list_of_dicts(self):
        issues = [_make_issue(1)]
        processed = preprocess_issues(issues)
        payload = issues_to_llm_payload(processed)
        assert isinstance(payload, list)
        assert isinstance(payload[0], dict)

    def test_expected_keys_present(self):
        issues = [_make_issue(1)]
        processed = preprocess_issues(issues)
        payload = issues_to_llm_payload(processed)
        expected_keys = {
            "number", "title", "body", "days_old", "days_since_update",
            "comments", "reactions", "labels", "is_stale", "good_first_issue",
            "is_assigned", "reporter_type", "milestone", "top_comments",
        }
        # created_at and html_url are carried on ProcessedIssue but excluded from LLM payload
        assert expected_keys == set(payload[0].keys())
