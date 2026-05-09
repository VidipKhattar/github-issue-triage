"""Integration test: validate LLM response parsing and report rendering."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

from triage.llm import _parse_json_from_text
from triage.models import TriageReport
from triage.reporter import render_table

_FIXTURE = Path(__file__).parent / "fixtures" / "sample_llm_response.json"


class TestFixtureRoundTrip:
    """Prove that a realistic LLM response parses, validates, and renders."""

    def _load_fixture(self) -> dict:
        return json.loads(_FIXTURE.read_text())

    def test_fixture_validates_as_triage_report(self):
        data = self._load_fixture()
        report = TriageReport.model_validate(data)
        assert report.repo == "owner/repo"
        assert report.total_issues_analyzed == 5
        assert len(report.top_priorities) == 3
        assert len(report.stale_issues) == 1
        assert len(report.quick_wins) == 1
        assert len(report.duplicate_groups) == 1
        assert len(report.clusters) == 2

    def test_priorities_have_required_fields(self):
        data = self._load_fixture()
        report = TriageReport.model_validate(data)
        for p in report.top_priorities:
            assert p.reasoning, f"Issue #{p.number} missing reasoning"
            assert p.suggested_action, f"Issue #{p.number} missing suggested_action"
            assert p.category, f"Issue #{p.number} missing category"
            assert 0.0 <= p.confidence <= 1.0

    def test_parse_json_from_text_extracts_fixture(self):
        raw = _FIXTURE.read_text()
        parsed = _parse_json_from_text(raw)
        assert parsed["repo"] == "owner/repo"
        assert len(parsed["top_priorities"]) == 3

    def test_parse_json_with_surrounding_prose(self):
        raw = "Here is the analysis:\n" + _FIXTURE.read_text() + "\nDone."
        parsed = _parse_json_from_text(raw)
        report = TriageReport.model_validate(parsed)
        assert report.total_issues_analyzed == 5

    def test_render_table_does_not_crash(self):
        data = self._load_fixture()
        report = TriageReport.model_validate(data)
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        # Monkey-patch the module-level console for this test
        import triage.reporter as mod
        original = mod.console
        mod.console = console
        try:
            render_table(report)
        finally:
            mod.console = original
        output = buf.getvalue()
        assert "Top Priorities" in output
        assert "Executive Summary" in output

    def test_json_output_round_trips(self):
        data = self._load_fixture()
        report = TriageReport.model_validate(data)
        dumped = report.model_dump()
        restored = TriageReport.model_validate(dumped)
        assert restored.repo == report.repo
        assert len(restored.top_priorities) == len(report.top_priorities)
