"""Skills — multi-step, goal-directed workflows above the tool layer.

Skills orchestrate primitive tools through LLM-driven agent calls.
They are the semantic layer between atomic tool operations and
user-facing CLI commands.

Registry:
    get_skill(name)     → Skill instance or None
    list_skills()       → list of (name, description)
    SKILL_REGISTRY      → dict of name → Skill class
"""

from __future__ import annotations

from noteweaver.skills.base import Skill, SkillContext, SkillResult
from noteweaver.skills.import_sources import ImportSources
from noteweaver.skills.organize_wiki import OrganizeWiki

SKILL_REGISTRY: dict[str, type[Skill]] = {
    "import_sources": ImportSources,
    "organize_wiki": OrganizeWiki,
}


def get_skill(name: str) -> Skill | None:
    """Look up a skill by name, returning an instance or None."""
    cls = SKILL_REGISTRY.get(name)
    return cls() if cls else None


def list_skills() -> list[tuple[str, str]]:
    """Return (name, description) pairs for all registered skills."""
    return [(name, cls().description) for name, cls in SKILL_REGISTRY.items()]


__all__ = [
    "Skill",
    "SkillContext",
    "SkillResult",
    "ImportSources",
    "OrganizeWiki",
    "SKILL_REGISTRY",
    "get_skill",
    "list_skills",
]
