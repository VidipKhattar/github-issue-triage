"""Pydantic model validation for TriageReport and related models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from triage.models import (
    DuplicateGroup,
    IssueCluster,
    IssuePriority,
    PriorityLevel,
    QuickWin,
    StaleIssue,
    TriageReport,
)


def _make_priority(**kwargs) -> dict:
    """Return a minimal valid IssuePriority dict, with overrides applied."""
    base = {
        "number": 1,
        "title": "Test issue",
        "priority": "high",
        "confidence": 0.85,
        "reasoning": "High impact on users.",
        "category": "bug",
        "suggested_action": "Assign to maintainer.",
    }
    base.update(kwargs)
    return base


class TestPriorityLevel:
    def test_all_values_valid(self):
        for value in ("critical", "high", "medium", "low"):
            assert PriorityLevel(value) is not None

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            PriorityLevel("urgent")


class TestIssuePriority:
    def test_valid_priority(self):
        p = IssuePriority(**_make_priority())
        assert p.priority == "high"
        assert p.confidence == 0.85
        assert p.category == "bug"
        assert p.suggested_action == "Assign to maintainer."

    def test_html_url_defaults_to_empty(self):
        p = IssuePriority(**_make_priority())
        assert p.html_url == ""

    def test_confidence_too_high_raises(self):
        with pytest.raises(ValidationError):
            IssuePriority(**_make_priority(confidence=1.5))

    def test_confidence_negative_raises(self):
        with pytest.raises(ValidationError):
            IssuePriority(**_make_priority(confidence=-0.1))

    def test_invalid_priority_value_raises(self):
        with pytest.raises(ValidationError):
            IssuePriority(**_make_priority(priority="urgent"))

    def test_missing_reasoning_raises(self):
        data = _make_priority()
        del data["reasoning"]
        with pytest.raises(ValidationError):
            IssuePriority(**data)

    def test_missing_suggested_action_raises(self):
        data = _make_priority()
        del data["suggested_action"]
        with pytest.raises(ValidationError):
            IssuePriority(**data)

    def test_all_priority_levels_accepted(self):
        for level in ("critical", "high", "medium", "low"):
            p = IssuePriority(**_make_priority(priority=level))
            assert p.priority == level


class TestIssueCluster:
    def test_valid_cluster(self):
        cluster = IssueCluster(
            theme="Performance",
            issue_numbers=[1, 2, 3],
            summary="Several slowdowns reported in rendering.",
        )
        assert cluster.theme == "Performance"
        assert len(cluster.issue_numbers) == 3

    def test_empty_issue_numbers_allowed(self):
        cluster = IssueCluster(theme="Misc", issue_numbers=[], summary="...")
        assert cluster.issue_numbers == []

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            IssueCluster(issue_numbers=[1], summary="Missing theme")


class TestStaleIssue:
    def test_valid(self):
        s = StaleIssue(number=7, title="Old bug", reason="No activity in 200 days.")
        assert s.number == 7


class TestQuickWin:
    def test_valid(self):
        q = QuickWin(number=3, title="Fix typo", why_quick="One-line doc change.")
        assert q.number == 3


class TestDuplicateGroup:
    def test_valid(self):
        d = DuplicateGroup(
            issue_numbers=[10, 11, 12],
            canonical_number=10,
            reasoning="All describe the same login bug.",
        )
        assert d.canonical_number == 10


class TestTriageReport:
    def _minimal_report(self) -> dict:
        return {"repo": "owner/repo", "total_issues_analyzed": 20}

    def test_minimal_valid_report(self):
        report = TriageReport(**self._minimal_report())
        assert report.repo == "owner/repo"
        assert report.clusters == []
        assert report.top_priorities == []

    def test_full_report(self):
        data = {
            "repo": "owner/repo",
            "total_issues_analyzed": 3,
            "summary": "Three open issues needing attention.",
            "clusters": [{"theme": "Bugs", "issue_numbers": [1, 2], "summary": "Two bugs"}],
            "top_priorities": [_make_priority(number=1, title="Crash on launch")],
            "stale_issues": [
                {"number": 3, "title": "Old feature", "reason": "Stale for 6 months."}
            ],
            "quick_wins": [{"number": 2, "title": "Typo fix", "why_quick": "One line."}],
            "duplicate_groups": [
                {"issue_numbers": [1, 2], "canonical_number": 1, "reasoning": "Same crash."}
            ],
        }
        report = TriageReport.model_validate(data)
        assert len(report.clusters) == 1
        assert report.top_priorities[0].priority in {"critical", "high", "medium", "low"}
        assert report.top_priorities[0].confidence == 0.85
        assert len(report.stale_issues) == 1
        assert len(report.quick_wins) == 1
        assert len(report.duplicate_groups) == 1

    def test_missing_repo_raises(self):
        with pytest.raises(ValidationError):
            TriageReport(total_issues_analyzed=5)

    def test_model_dump_round_trip(self):
        report = TriageReport(repo="a/b", total_issues_analyzed=0)
        dumped = report.model_dump()
        restored = TriageReport.model_validate(dumped)
        assert restored.repo == report.repo

    def test_json_schema_valid(self):
        schema = TriageReport.model_json_schema()
        assert "properties" in schema
        assert "repo" in schema["properties"]
