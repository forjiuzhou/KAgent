"""Seed templates written during ``vault init``.

These are pure string constants — no runtime behavior.  Editing these
changes what a fresh vault looks like; it does not affect existing vaults.
"""

INITIAL_SCHEMA = """\
---
title: Wiki Schema
type: preference
updated: {date}
---

# Wiki Schema

Structure definition for this knowledge base. The agent loads this at
startup to understand how the wiki is organized.

For behavioral rules (how to read, write, maintain), see protocols.md.
For user preferences (language, style), see preferences.md.

## Core Principle: Progressive Disclosure

The wiki is a tree (top-down hierarchy) overlaid with a graph
(cross-references) and tags (horizontal slicing):

```
index.md  (root — lists Hubs, kept under ~1000 tokens)
  → Hub   (topic entry — overview + child page links)
    → Canonical / Note / Synthesis  (content pages)
```

Three navigation mechanisms:
- **Tree** (index → Hub → Page): structured, top-down
- **Tags** (frontmatter `tags` field): cross-cutting, horizontal
- **Links** ([[wiki-links]]): associative, point-to-point

**Inverted pyramid**: every page's first 1-2 sentences are a
self-contained summary. Reading only summaries should be enough
to judge relevance.

## Page Types

| Type | Role | Key rules |
|------|------|-----------|
| `hub` | Navigation entry for a topic | Concise. Lists child pages with one-line descriptions. No deep content. |
| `canonical` | Authoritative document on a topic | MUST have `sources`. One per topic. |
| `note` | Work-in-progress | Low barrier. Can be revised, merged, promoted. Duplicates OK. |
| `synthesis` | Cross-cutting analysis | Must cite ≥2 sources via [[wiki-links]]. |
| `journal` | Time-ordered captures, daily logs | Preserve original expression. Low-barrier entry. |
| `archive` | Retired page | Soft-deleted. Never hard-delete — always archive. |

Hub says "here's everything about X, go read these pages."
Canonical says "here's the definitive explanation of X."
If a page grows both navigation AND deep content, split it.

## Frontmatter

Required on all wiki pages (except index.md and log.md):

```yaml
---
title: Page Title
type: hub | canonical | note | synthesis | journal | archive
summary: One-sentence description of what this page covers
tags: [topic-a, topic-b]
sources: []          # required for canonical
related: []          # [[wiki-links]]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

## Tags

Tags provide horizontal navigation across the tree. Create and
manage tags organically — no predefined taxonomy. Tags emerge
from content.

Special tag: `pinned` — these pages appear at the top of index.md.

## Writing Style

- File names: lowercase-hyphenated (e.g. `attention-mechanism.md`)
- Inverted pyramid: first 1-2 sentences = self-contained summary
- Every page ends with `## Related` listing [[wiki-links]]
- Hub pages: short overview, then [[link]] list with descriptions
- Canonical pages: summary → evidence → analysis → ## Related

## Directory Layout

```
vault/
├── sources/          immutable raw materials (read-only)
├── wiki/
│   ├── index.md      navigation root (lists Hubs only)
│   ├── log.md        operation log
│   ├── concepts/     hub, canonical, note pages
│   ├── journals/     daily entries, quick captures
│   ├── synthesis/    analysis, cross-cutting pages
│   └── archive/      retired pages
└── .schema/
    ├── schema.md       this file — wiki structure definition
    ├── protocols.md    behavioral rules for agents
    └── preferences.md  user preferences
```

## Journal → Knowledge Pipeline

Journals are the raw material pool. The promotion flow:

```
Conversation → Journal (raw capture, low barrier)
                 ↓
              Digest (periodic review, extracts insights)
                 ↓
           Note / Canonical (structured knowledge)
```
"""

INITIAL_PROTOCOLS = """\
---
title: Protocols
type: preference
updated: {date}
---

# Protocols

Behavioral rules for agents operating on this vault.
These are hard constraints and high-leverage workflow patterns —
not preferences, not suggestions.

## Observation Protocols

- **Read before write.** Always read a page before modifying it.
- **Search before create.** Before creating a new page, search for
  existing pages on the same topic. Prefer updating or appending
  to an existing page over creating a duplicate.
- **Scan before restructure.** Before any structural maintenance
  (hub creation, reorganization, bulk linking), read the world
  summary and understand the current shape of the wiki.

## Structure Protocols

- Every durable page (hub, canonical, note, synthesis) must have
  frontmatter with at least `title`, `type`, and `summary`.
- Every durable page should end with `## Related` containing
  [[wiki-links]] to connected pages.
- Canonical pages must have a non-empty `sources` field.
- When 3+ pages accumulate on a topic with no hub, create a hub.
- No orphan pages: every new page must link to at least one
  existing page, and at least one existing page should link back.
- Hub pages are navigation entries — keep them concise, list child
  pages with one-line descriptions, don't put deep content in hubs.

## Change Protocols

- **Small changes: brief notice then write.** Appending a section,
  adding a link, updating tags or metadata — briefly tell the user
  what you're about to do, then write. No need to wait for approval.
- **Larger changes: propose first.** Creating new pages or
  restructuring existing content — describe your plan in natural
  language and let the user confirm before writing.
- **When uncertain, ask.** If there are trade-offs or the user's
  intent is ambiguous, propose and ask rather than guess.
- **Journal is low-barrier.** Journal entries can be written freely
  without full structural compliance — they are raw material.
- **Never hard-delete.** Durable pages are never deleted, only
  archived via the archive mechanism.
- **Sources are immutable.** Never write to `sources/` — it is a
  read-only reference library.

## Conversation-to-Wiki Protocol

When a conversation produces an insight worth capturing:

1. Search existing wiki for related pages.
2. If a related canonical or note exists, propose appending or
   updating it — don't create a duplicate.
3. If it's genuinely new, create a note (not canonical — notes
   are the low-barrier entry point for new knowledge).
4. Add [[wiki-links]] connecting the new content to existing pages.
5. Check if a hub needs updating or creating.
6. Confirm the structural result: no orphans, links are bidirectional.

## Source Import Protocol

When importing external content (URL, file, etc.):

1. Fetch and save the raw source to `sources/`.
2. Search existing wiki to understand what already covers this topic.
3. Create or update wiki pages that synthesize the source material.
4. Link new pages to existing related pages.
5. If a hub exists for this topic, update it. If 3+ pages now exist
   without a hub, create one.
6. Update `wiki/index.md` if new hubs were created.
"""

INITIAL_PREFERENCES = """\
---
title: User Preferences
type: preference
updated: {date}
---

# User Preferences

This file tells the agent how you want it to behave. Edit it anytime.
The agent reads this at startup and adapts accordingly.

## Language
- Respond in: (auto-detect from user input)

## Organization Style
- (default: organize by topic, create Hubs when 3+ pages accumulate)

## Other Preferences
- (add your preferences here as you discover them)
"""

INITIAL_INDEX = """\
---
title: Wiki Index
updated: {date}
---

# Wiki Index

Root of the knowledge base. Start here to navigate.

## Topics

(no hubs yet — as content grows, the agent creates Hub pages here)

## Recent

(no pages yet)
"""

INITIAL_LOG = """\
---
title: Operation Log
---

# Operation Log

Chronological record of all agent operations.

## [{date}] init | Vault Created

Vault initialized. Ready for knowledge.
"""
