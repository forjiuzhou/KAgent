"""Configuration management.

Config sources (later overrides earlier):
1. Vault config file (.meta/config.yaml)
2. Environment variables (OPENAI_API_KEY, ANTHROPIC_API_KEY, NW_MODEL, etc.)

Provider detection:
- NW_PROVIDER=openai|anthropic  (explicit override)
- ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN set → anthropic
- OPENAI_API_KEY set                           → openai (default)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"

DEFAULT_MODELS = {
    PROVIDER_OPENAI: "gpt-4o-mini",
    PROVIDER_ANTHROPIC: "claude-sonnet-4-5-20250929",
}


def _anthropic_key_from_env() -> str:
    """Read Anthropic API key from environment, checking both common names."""
    return (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or ""
    )


@dataclass
class Config:
    provider: str = PROVIDER_OPENAI
    api_key: str = ""
    base_url: str = ""
    model: str = ""

    @classmethod
    def load(cls, vault_path: Path) -> Config:
        """Load config from file + environment, env vars take precedence."""
        cfg = cls()

        config_file = vault_path / ".meta" / "config.yaml"
        if config_file.is_file():
            try:
                data = yaml.safe_load(config_file.read_text()) or {}
                cfg.provider = data.get("provider", cfg.provider)
                cfg.api_key = data.get("api_key", "")
                cfg.base_url = data.get("base_url", "")
                cfg.model = data.get("model", "")
            except Exception:
                pass

        # Detect provider from environment
        explicit_provider = os.environ.get("NW_PROVIDER", "").lower()
        has_anthropic_key = bool(_anthropic_key_from_env())
        has_openai_key = bool(os.environ.get("OPENAI_API_KEY"))

        if explicit_provider in (PROVIDER_OPENAI, PROVIDER_ANTHROPIC):
            cfg.provider = explicit_provider
        elif has_anthropic_key and not has_openai_key:
            cfg.provider = PROVIDER_ANTHROPIC
        elif has_openai_key:
            cfg.provider = PROVIDER_OPENAI

        # Load provider-specific env vars
        if cfg.provider == PROVIDER_ANTHROPIC:
            anthropic_key = _anthropic_key_from_env()
            if anthropic_key:
                cfg.api_key = anthropic_key
            if os.environ.get("ANTHROPIC_BASE_URL"):
                cfg.base_url = os.environ["ANTHROPIC_BASE_URL"]
        else:
            if os.environ.get("OPENAI_API_KEY"):
                cfg.api_key = os.environ["OPENAI_API_KEY"]
            if os.environ.get("OPENAI_BASE_URL"):
                cfg.base_url = os.environ["OPENAI_BASE_URL"]

        if os.environ.get("NW_MODEL"):
            cfg.model = os.environ["NW_MODEL"]

        # Fall back to provider default model
        if not cfg.model:
            cfg.model = DEFAULT_MODELS.get(cfg.provider, DEFAULT_MODELS[PROVIDER_OPENAI])

        return cfg

    def save(self, vault_path: Path) -> None:
        """Persist non-sensitive config to vault config file."""
        config_file = vault_path / ".meta" / "config.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if self.provider != PROVIDER_OPENAI:
            data["provider"] = self.provider
        default_model = DEFAULT_MODELS.get(self.provider, "gpt-4o-mini")
        if self.model != default_model:
            data["model"] = self.model
        if self.base_url:
            data["base_url"] = self.base_url
        # Intentionally NOT saving api_key to disk
        if data:
            config_file.write_text(yaml.dump(data, default_flow_style=False))
