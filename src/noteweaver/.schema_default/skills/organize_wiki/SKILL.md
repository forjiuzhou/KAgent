---
name: organize_wiki
description: Audit and fix wiki health issues — metadata, links, hubs, duplicates, orphans, and tag consistency.
---

# Organize Wiki

You are performing a wiki health check and remediation.

## When to use

The user asks to clean up, organize, fix, audit, lint, or maintain the wiki.
Examples:

- "整理一下知识库"
- "检查 wiki 有没有问题"
- "clean up the wiki structure"
- "fix orphan pages and missing links"

## Workflow

### Phase 1: Audit

1. Use `list_pages("wiki")` to get all wiki pages with their metadata.
2. Identify issues:
   - **Orphan pages**: pages with no incoming or outgoing links
   - **Missing summaries**: pages with empty or missing summary in frontmatter
   - **Broken links**: `[[wiki-links]]` that reference non-existent pages
   - **Tag inconsistencies**: similar or duplicate tags across pages
   - **Hub candidates**: 3+ pages sharing a tag but no hub page exists
   - **Missing connections**: pages on related topics that aren't linked

### Phase 2: Fix

For each issue category:

1. **Read** the affected page(s) with `read_page(path)`.
2. **Fix** using the appropriate tool:
   - Missing/bad metadata → `update_frontmatter(path, fields)`
   - Missing links → `add_related_link(path, link_to)`
   - New hub needed → `write_page(path, content)` with type: hub
   - Orphan pages → `add_related_link` to connect them to a hub or related page
   - Similar tags → choose one canonical tag and `update_frontmatter` across pages
3. **Verify** — always read a page before writing to it.

### Phase 3: Report

After fixing issues, give a brief summary:
- How many issues were found
- What was fixed
- What still needs attention (if anything)

## Rules

- Always read a page before writing to it.
- Preserve existing content when updating metadata.
- Write in the user's language.
- Process ALL issues — do not stop partway to ask for confirmation.
