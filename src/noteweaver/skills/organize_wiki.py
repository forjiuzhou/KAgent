"""Skill: Organize Wiki — full-scope wiki health check and remediation.

Replaces the old ``nw lint`` LLM remediation prompt (which referenced
non-existent ``organize()`` / ``restructure()`` / ``capture()`` tools)
and the legacy ``restructure`` handler.

Workflow:
1. **Prepare** (deterministic): run ``vault.audit_vault()`` to find
   structural issues — stale imports, missing summaries, orphan pages,
   broken links, tag inconsistencies, hub candidates.
2. **Execute** (LLM-driven): feed the agent a crafted prompt with the
   audit report.  The agent uses primitive tools to fix issues — updating
   frontmatter, adding links, archiving stale pages, creating hubs, etc.
3. **Report**: summary of fixes applied.
"""

from __future__ import annotations

import json as _json
from typing import Generator

from noteweaver.skills.base import Skill, SkillContext, SkillResult


class OrganizeWiki(Skill):
    """Audit and remediate wiki health issues."""

    @property
    def name(self) -> str:
        return "organize_wiki"

    @property
    def description(self) -> str:
        return "Audit the wiki for health issues and fix them: metadata, links, hubs, duplicates, orphans."

    def prepare(self, ctx: SkillContext, **kwargs) -> str | None:
        """Run a deterministic vault audit.

        Returns a scope summary if issues are found, None otherwise.
        """
        vault = ctx.vault

        report = vault.audit_vault()
        vault.save_audit_report(report)

        self._last_report = report

        summary = report.get("summary", "")
        if "0 issues" in summary:
            return None

        issue_counts = []
        for key, label in [
            ("stale_imports", "stale imports"),
            ("hub_candidates", "hub candidates"),
            ("orphan_pages", "orphans"),
            ("missing_summaries", "missing summaries"),
            ("broken_links", "broken links"),
            ("missing_connections", "missing connections"),
            ("similar_tags", "similar tags"),
        ]:
            items = report.get(key, [])
            if items:
                issue_counts.append(f"{len(items)} {label}")

        return f"Audit: {', '.join(issue_counts)}." if issue_counts else None

    def _build_prompt(self, ctx: SkillContext, **kwargs) -> str:
        """Build the LLM prompt from the audit report."""
        report = getattr(self, "_last_report", None)
        if report is None:
            report = ctx.vault.audit_vault()

        focus = kwargs.get("focus")

        report_text = _json.dumps(report, indent=2, ensure_ascii=False)

        focus_instruction = ""
        if focus:
            focus_instruction = (
                f"\n**Focus area:** Only address '{focus}' issues this time. "
                "Skip other categories.\n"
            )

        return (
            "## Task: Organize and fix wiki health issues\n\n"
            "The vault audit found the following issues:\n\n"
            f"```json\n{report_text}\n```\n\n"
            f"{focus_instruction}"
            "## Instructions\n\n"
            "Fix ALL issues you can. For each issue:\n\n"
            "1. **Read** the affected page(s) with `read_page(path)`.\n"
            "2. **Fix** using the appropriate tool:\n"
            "   - Missing/bad metadata → `update_frontmatter(path, fields)`\n"
            "   - Missing links → `add_related_link(path, link_to)`\n"
            "   - New hub needed → `write_page(path, content)` with type: hub\n"
            "   - Orphan pages → `add_related_link` to connect them to a hub\n"
            "   - Broken wiki-links → `update_frontmatter` or edit via `write_page`\n"
            "   - Stale imports → `read_page` then `write_page` with proper "
            "frontmatter and content restructuring\n"
            "   - Similar tags → choose one canonical tag and `update_frontmatter` "
            "across affected pages\n"
            "3. **Verify** — if unsure about a fix, read the page first.\n\n"
            "## Rules\n\n"
            "- Process ALL issues — do not stop partway.\n"
            "- Always read a page before writing to it.\n"
            "- Preserve existing content when updating metadata.\n"
            "- Write in the user's language.\n"
            "- After fixing all issues, give a brief summary of what was done."
        )

    def execute(self, ctx: SkillContext, **kwargs) -> Generator[str, None, SkillResult]:
        """Drive the agent to fix audit issues."""
        prompt = self._build_prompt(ctx, **kwargs)

        if ctx.dry_run:
            yield "[dry-run] Would send prompt to agent"
            return SkillResult(
                skill_name=self.name,
                success=True,
                summary="Dry run — no changes made.",
            )

        items_processed = 0
        items_succeeded = 0
        details: list[str] = []
        last_reply = ""

        for chunk in ctx.agent.chat(prompt):
            yield chunk
            if chunk.startswith("  ↳ "):
                items_processed += 1
                tool_info = chunk.strip()
                if any(w in tool_info for w in (
                    "write_page", "append_section",
                    "update_frontmatter", "add_related_link",
                )):
                    items_succeeded += 1
                details.append(tool_info)
            elif not chunk.startswith("  📋 "):
                last_reply = chunk

        return SkillResult(
            skill_name=self.name,
            success=True,
            summary=last_reply[:500] if last_reply else f"Applied {items_succeeded} fixes.",
            items_processed=items_processed,
            items_succeeded=items_succeeded,
            details=details,
        )
