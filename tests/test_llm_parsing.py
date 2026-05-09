"""Tests for LLM response parsing edge cases."""

from __future__ import annotations

import pytest

from triage.llm import _parse_json_from_text


class TestParseJsonFromText:
    """Edge cases for extracting JSON from raw LLM responses."""

    def test_clean_json(self):
        raw = '{"repo": "a/b", "total_issues_analyzed": 1}'
        result = _parse_json_from_text(raw)
        assert result["repo"] == "a/b"

    def test_json_with_markdown_fences(self):
        raw = '```json\n{"repo": "a/b", "total_issues_analyzed": 1}\n```'
        result = _parse_json_from_text(raw)
        assert result["repo"] == "a/b"

    def test_json_with_surrounding_prose(self):
        raw = (
            "Here is the triage report:\n\n"
            '{"repo": "a/b", "total_issues_analyzed": 1}\n\n'
            "Let me know if you need changes."
        )
        result = _parse_json_from_text(raw)
        assert result["repo"] == "a/b"

    def test_json_with_leading_whitespace(self):
        raw = '   \n\n  {"repo": "a/b", "total_issues_analyzed": 1}  \n'
        result = _parse_json_from_text(raw)
        assert result["repo"] == "a/b"

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="no JSON object"):
            _parse_json_from_text("This response has no JSON at all.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="no JSON object"):
            _parse_json_from_text("")

    def test_truncated_json_raises_value_error(self):
        raw = '{"repo": "a/b", "top_priorities": ['
        with pytest.raises(ValueError, match="no JSON object"):
            _parse_json_from_text(raw)

    def test_nested_objects_parsed(self):
        raw = '{"repo": "a/b", "total_issues_analyzed": 1, "clusters": [{"theme": "bugs", "issue_numbers": [1], "summary": "one bug"}]}'
        result = _parse_json_from_text(raw)
        assert len(result["clusters"]) == 1
        assert result["clusters"][0]["theme"] == "bugs"
