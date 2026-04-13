"""Base class and context for skills.

A Skill is a multi-step, goal-directed workflow that orchestrates primitive
tools through LLM-driven agent calls.  Skills sit above tools (which are
atomic operations) and below CLI commands (which are user-facing entry points).

Skills have:
- A deterministic *prepare* phase: scan files, audit vault, compute scope
- An LLM-driven *execute* phase: agent.chat() with a crafted prompt
- A *report* phase: summarise what happened

Tools are the hands; skills are the arms.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from noteweaver.agent import KnowledgeAgent
    from noteweaver.vault import Vault


@dataclass
class SkillResult:
    """Outcome of a skill execution."""

    skill_name: str
    success: bool
    summary: str
    items_processed: int = 0
    items_succeeded: int = 0
    details: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class SkillContext:
    """Runtime context passed to every skill.

    Contains the vault, agent, and execution parameters.
    Skills should not reach outside this context.
    """

    vault: Vault
    agent: KnowledgeAgent
    attended: bool = True
    dry_run: bool = False


class Skill(ABC):
    """Abstract base class for all skills.

    Subclasses implement:
    - name / description (class attrs or properties)
    - prepare()  — deterministic pre-check, returns a scope summary
    - execute()  — LLM-driven execution, yields progress strings
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Machine-readable skill name (e.g. 'import_sources')."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line human-readable description."""

    @abstractmethod
    def prepare(self, ctx: SkillContext, **kwargs) -> str | None:
        """Deterministic pre-check.  Scan the vault, compute scope.

        Returns a human-readable scope summary, or None if there's
        nothing to do.  This should NOT call the LLM.
        """

    @abstractmethod
    def execute(self, ctx: SkillContext, **kwargs) -> Generator[str, None, SkillResult]:
        """Run the skill.  Yields progress strings, returns SkillResult.

        The implementation should call agent.chat() with a carefully
        crafted prompt, yield tool call and reply strings as they come,
        and return a final SkillResult.
        """

    def run(self, ctx: SkillContext, **kwargs) -> Generator[str, None, SkillResult]:
        """Full lifecycle: prepare → execute → report.

        Callers iterate the generator for progress, and the generator
        returns the final SkillResult.
        """
        t0 = time.monotonic()

        scope = self.prepare(ctx, **kwargs)
        if scope is None:
            return SkillResult(
                skill_name=self.name,
                success=True,
                summary="Nothing to do.",
            )

        yield f"[{self.name}] {scope}"

        result = yield from self.execute(ctx, **kwargs)
        result.duration_ms = (time.monotonic() - t0) * 1000
        return result
