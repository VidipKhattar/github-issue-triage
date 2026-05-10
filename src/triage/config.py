"""Settings loaded from environment via python-dotenv."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Settings:
    """Central config object; all values read from environment at access time."""

    _PROVIDER_ENV_KEYS: dict[str, str] = {
        "claude": "MODEL_CLAUDE",
        "gpt": "MODEL_OPENAI",
        "gemini": "MODEL_GEMINI",
    }

    _PROVIDER_DEFAULTS: dict[str, str] = {
        "claude": "claude-sonnet-4-20250514",
        "gpt": "gpt-4o",
        "gemini": "gemini/gemini-1.5-pro",
    }

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
    def default_provider(self) -> str:
        """Active provider from ``DEFAULT_PROVIDER``, defaulting to ``claude``."""
        return os.getenv("DEFAULT_PROVIDER", "claude").lower()

    def model_for_provider(self, provider: str) -> str:
        """Return the LiteLLM model string for the given provider shorthand.

        Reads the provider-specific env var (e.g. ``MODEL_CLAUDE``) and falls
        back to a sensible default when not set.

        Args:
            provider: One of ``"claude"``, ``"gpt"``, or ``"gemini"``.

        Returns:
            A LiteLLM-compatible model string.

        Raises:
            ValueError: If ``provider`` is not a recognised shorthand.
        """
        if provider not in self._PROVIDER_ENV_KEYS:
            raise ValueError(
                f"Unknown provider '{provider}'. Choose from: "
                + ", ".join(self._PROVIDER_ENV_KEYS)
            )
        env_key = self._PROVIDER_ENV_KEYS[provider]
        return os.getenv(env_key, self._PROVIDER_DEFAULTS[provider])

    @property
    def litellm_model(self) -> str:
        """LiteLLM model string resolved from the default provider.

        Equivalent to ``model_for_provider(default_provider)``. Override the
        active model by setting ``DEFAULT_PROVIDER`` and the corresponding
        ``MODEL_CLAUDE`` / ``MODEL_OPENAI`` / ``MODEL_GEMINI`` env vars.
        """
        return self.model_for_provider(self.default_provider)


settings = Settings()
