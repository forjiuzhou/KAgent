"""agent/ — the core agent loop.

Public API: ``from noteweaver.agent import KnowledgeAgent, create_provider``
"""

from noteweaver.agent.core import KnowledgeAgent, _msg_role, _msg_content
from noteweaver.agent.prompts import (
    PROMPT_IDENTITY,
    PROMPT_TOOLS,
    PROMPT_SKILLS_HEADER,
    SYSTEM_PROMPT_BASE,
    _format_available_skills,
    create_provider,
)

__all__ = [
    "KnowledgeAgent",
    "create_provider",
    "_format_available_skills",
    "PROMPT_IDENTITY",
    "PROMPT_TOOLS",
    "PROMPT_SKILLS_HEADER",
    "SYSTEM_PROMPT_BASE",
    "_msg_role",
    "_msg_content",
]
