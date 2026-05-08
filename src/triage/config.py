"""Settings loaded from environment via python-dotenv."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Settings:
    """Central config object; all values read from environment at access time."""

    @property
    def github_token(self) -> str | None:
        """Optional GitHub personal access token from ``GITHUB_TOKEN``."""
        return os.getenv("GITHUB_TOKEN")

    @property
    def since_default(self) -> int:
        """Default lookback window in days from ``SINCE_DEFAULT``, defaulting to ``7``."""
        return int(os.getenv("SINCE_DEFAULT", "7"))

    @property
    def max_issues(self) -> int:
        """Maximum issues to fetch per run from ``MAX_ISSUES``, defaulting to ``500``."""
        return int(os.getenv("MAX_ISSUES", "500"))

    @property
    def litellm_model(self) -> str:
        """LiteLLM model string from ``LITELLM_MODEL``.

        Controls which LLM and provider are used. Examples:
            ``claude-sonnet-4-20250514``, ``gpt-4o``, ``gemini/gemini-1.5-pro``
        """
        return os.getenv("LITELLM_MODEL", "claude-sonnet-4-20250514")


settings = Settings()
