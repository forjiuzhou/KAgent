"""Configuration management.

Config sources (later overrides earlier):
1. Vault config file (.meta/config.yaml)
2. Environment variables (OPENAI_API_KEY, NW_MODEL, etc.)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-4o-mini"

    @classmethod
    def load(cls, vault_path: Path) -> Config:
        """Load config from file + environment, env vars take precedence."""
        cfg = cls()

        config_file = vault_path / ".meta" / "config.yaml"
        if config_file.is_file():
            try:
                data = yaml.safe_load(config_file.read_text()) or {}
                cfg.api_key = data.get("api_key", "")
                cfg.base_url = data.get("base_url", "")
                cfg.model = data.get("model", cfg.model)
            except Exception:
                pass

        if os.environ.get("OPENAI_API_KEY"):
            cfg.api_key = os.environ["OPENAI_API_KEY"]
        if os.environ.get("OPENAI_BASE_URL"):
            cfg.base_url = os.environ["OPENAI_BASE_URL"]
        if os.environ.get("NW_MODEL"):
            cfg.model = os.environ["NW_MODEL"]

        return cfg

    def save(self, vault_path: Path) -> None:
        """Persist non-sensitive config to vault config file."""
        config_file = vault_path / ".meta" / "config.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if self.model != "gpt-4o-mini":
            data["model"] = self.model
        if self.base_url:
            data["base_url"] = self.base_url
        # Intentionally NOT saving api_key to disk
        if data:
            config_file.write_text(yaml.dump(data, default_flow_style=False))
