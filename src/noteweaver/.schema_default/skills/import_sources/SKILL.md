---
name: import_sources
description: Import and restructure a source folder into a structured knowledge base with proper wiki pages, frontmatter, links, and topic hubs.
---

# Import Sources

You are performing a knowledge base reconstruction from source files.

## When to use

The user asks to import, process, organize, restructure, or build a knowledge
base from a folder, directory, or collection of source files.  Examples:

- "把 sources/typora 导入并整理成完整知识库"
- "把这个文件夹里的内容重构进 wiki"
- "帮我导入 sources 里的文件"
- "import and organize these notes"

## Workflow

### Phase 1: Scan

1. Use `list_pages(directory="sources", include_raw=true)` to find all source files.
2. Use `list_pages(directory="wiki")` to understand the current wiki structure.
3. Identify which source files have NOT yet been processed (not referenced in
   any wiki page's frontmatter or content).

### Phase 2: Cluster

Before processing files individually, think about the **topic structure**:

1. Read a sample of source files with `read_page(path, max_chars=500)` to
   understand the content landscape.
2. Group source files into **topic clusters** — files that cover related
   subjects should be processed together.
3. For each cluster, decide the target wiki structure:
   - Should it become a new **hub** page?
   - Should it merge into existing wiki pages?
   - Should it create new **canonical** or **note** pages?

### Phase 3: Reconstruct (per cluster)

For each topic cluster, use `spawn_subagent` to process it in isolation:

```
spawn_subagent(task="Process the following topic cluster into wiki pages: ...")
```

The subagent task description should include:
- The list of source file paths in this cluster
- The current wiki structure summary (hubs, key pages)
- The target output (new pages to create, existing pages to extend)
- Instructions to use `read_page`, `write_page`, `append_section`,
  `add_related_link` as needed

Each subagent will:
1. Read the source files in its cluster
2. Create or update wiki pages with proper YAML frontmatter
   (title, type, summary, tags, created, updated)
3. Place pages in the right directory (wiki/concepts/, wiki/synthesis/)
4. Add `[[wiki-links]]` via `add_related_link` to connect related pages

### Phase 4: Consolidate

After all clusters are processed:

1. Use `list_pages("wiki")` to review the full wiki state.
2. Check for orphan pages that lack connections — use `add_related_link`.
3. Check if any hub pages need updating with new child links.
4. Rebuild the index if needed with `write_page("wiki/index.md", ...)`.
5. Report a summary of what was created, updated, and linked.

## Rules

- Process ALL source files — do not stop partway.
- Write in the user's language (match the source content language).
- Synthesize and restructure — don't just copy-paste raw content.
- Group related content into a single wiki page when it makes sense.
- Every new wiki page MUST have valid YAML frontmatter.
- Use `spawn_subagent` for each cluster to avoid running out of steps.
