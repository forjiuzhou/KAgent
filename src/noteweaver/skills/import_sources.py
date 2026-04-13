"""Skill: Import Sources — bulk-import files from sources/ into wiki/.

This is the first skill introduced in NoteWeaver.  It replaces the
old ``ingest(source_type='directory')`` legacy handler and the CLI
``nw import`` + ad-hoc ``nw ingest`` prompt that referenced non-existent
tools.

Workflow:
1. **Prepare** (deterministic): scan sources/ for unprocessed files,
   cross-reference with wiki/ to find what's already been imported,
   produce a scope summary.
2. **Execute** (LLM-driven): feed the agent a crafted prompt with
   the file listing and instructions.  The agent uses primitive tools
   (read_page, write_page, append_section, search, etc.) to process
   each file — reading source content, deciding on wiki structure,
   writing pages, and linking them.
3. **Report**: summary of pages created/updated.
"""

from __future__ import annotations

from typing import Generator

from noteweaver.skills.base import Skill, SkillContext, SkillResult


class ImportSources(Skill):
    """Bulk-import source files into the wiki as structured knowledge."""

    @property
    def name(self) -> str:
        return "import_sources"

    @property
    def description(self) -> str:
        return "Import source files into the wiki, creating structured pages with proper frontmatter and links."

    def prepare(self, ctx: SkillContext, **kwargs) -> str | None:
        """Scan sources/ for files not yet reflected in wiki/.

        Returns a scope summary string, or None if nothing to import.
        """
        source_dir = kwargs.get("source_dir", "sources")
        vault = ctx.vault

        source_files = vault.list_all_files(source_dir)
        if not source_files:
            return None

        wiki_frontmatters = vault.read_frontmatters("wiki")
        existing_sources = set()
        for fm in wiki_frontmatters:
            for src in (fm.get("sources") or []):
                existing_sources.add(src)
            if fm.get("summary", "").startswith("Imported from "):
                existing_sources.add(fm["path"])

        unprocessed = []
        for f in source_files:
            if f["path"] not in existing_sources:
                unprocessed.append(f)

        if not unprocessed:
            return None

        total_size = sum(f["size_bytes"] for f in unprocessed)
        size_str = (
            f"{total_size / 1024:.1f}KB" if total_size < 1024 * 1024
            else f"{total_size / (1024 * 1024):.1f}MB"
        )

        return (
            f"Found {len(unprocessed)} unprocessed source file(s) "
            f"in {source_dir}/ ({size_str} total). "
            f"Wiki has {len(wiki_frontmatters)} existing pages."
        )

    def _build_prompt(self, ctx: SkillContext, **kwargs) -> str:
        """Build the LLM prompt for the import task."""
        source_dir = kwargs.get("source_dir", "sources")
        vault = ctx.vault

        source_files = vault.list_all_files(source_dir)

        file_listing = []
        for f in source_files:
            size = f["size_bytes"]
            size_str = f"{size}B" if size < 1024 else f"{size / 1024:.1f}KB"
            file_listing.append(f"- {f['path']} ({size_str})")

        files_text = "\n".join(file_listing) if file_listing else "(empty)"

        return (
            f"## Task: Import source files into the wiki\n\n"
            f"There are {len(source_files)} file(s) in `{source_dir}/`:\n\n"
            f"{files_text}\n\n"
            "## Instructions\n\n"
            "Process ALL files in scope. For each source file:\n\n"
            "1. **Read** it with `read_page(path)` to understand the content.\n"
            "2. **Search** with `search(query)` to check if a similar topic "
            "already exists in the wiki.\n"
            "3. **Decide**: create a new wiki page, or append to an existing one.\n"
            "4. **Write** using `write_page(path, content)` for new pages, or "
            "`append_section(path, heading, content)` for additions.\n"
            "   - Every new page MUST have valid YAML frontmatter "
            "(title, type, summary, tags, created, updated).\n"
            "   - Choose the right type: note, canonical, synthesis.\n"
            "   - Place in the right directory: wiki/concepts/, wiki/synthesis/.\n"
            "5. **Link** with `add_related_link(path, link_to)` to connect "
            "related pages.\n\n"
            "## Rules\n\n"
            "- Process ALL files — do not stop partway to ask for confirmation.\n"
            "- Write in the user's language (match the source content language).\n"
            "- Synthesize and restructure — don't just copy-paste raw content.\n"
            "- If a source file is very long, extract the key points.\n"
            "- Group related content into a single wiki page when it makes sense.\n"
            "- After processing all files, give a brief summary of what was done."
        )

    def execute(self, ctx: SkillContext, **kwargs) -> Generator[str, None, SkillResult]:
        """Drive the agent to import all source files."""
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
                if "write_page" in tool_info or "append_section" in tool_info:
                    items_succeeded += 1
                details.append(tool_info)
            elif not chunk.startswith("  📋 "):
                last_reply = chunk

        return SkillResult(
            skill_name=self.name,
            success=True,
            summary=last_reply[:500] if last_reply else f"Processed {items_processed} tool calls.",
            items_processed=items_processed,
            items_succeeded=items_succeeded,
            details=details,
        )
