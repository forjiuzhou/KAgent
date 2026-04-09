"""Tests for content-layer write gates.

Covers:
- Read-before-write for all content-targeting tools
- Minimum body length for note pages
- Minimum [[wiki-link]] count for synthesis pages
- Preferences.md special gate
"""

from __future__ import annotations

import pytest

from noteweaver.tools.policy import (
    PolicyContext,
    check_pre_dispatch,
    MIN_NOTE_BODY_CHARS,
    MIN_SYNTHESIS_LINKS,
    _extract_type,
    _strip_frontmatter,
    _WIKI_LINK_RE,
)


# ======================================================================
# Read-before-write for content targets
# ======================================================================

class TestReadBeforeWrite:
    """All content-targeting tools (except write_page, which has its own
    heavier gate) must have the target page read first."""

    def test_append_section_blocked_without_read(self) -> None:
        ctx = PolicyContext(attended=True)
        v = check_pre_dispatch(
            "append_section",
            {"path": "wiki/concepts/attention.md", "heading": "New", "content": "..."},
            ctx,
        )
        assert not v.allowed
        assert "read_page" in (v.warning or "")

    def test_append_section_allowed_after_read(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("read_page", {"path": "wiki/concepts/attention.md"})
        v = check_pre_dispatch(
            "append_section",
            {"path": "wiki/concepts/attention.md", "heading": "New", "content": "..."},
            ctx,
        )
        assert v.allowed

    def test_append_to_section_blocked_without_read(self) -> None:
        ctx = PolicyContext(attended=True)
        v = check_pre_dispatch(
            "append_to_section",
            {"path": "wiki/concepts/ml.md", "heading": "Basics", "content": "..."},
            ctx,
        )
        assert not v.allowed

    def test_update_frontmatter_blocked_without_read(self) -> None:
        ctx = PolicyContext(attended=True)
        v = check_pre_dispatch(
            "update_frontmatter",
            {"path": "wiki/concepts/ml.md", "fields": {"tags": ["ml"]}},
            ctx,
        )
        assert not v.allowed

    def test_update_frontmatter_allowed_after_read(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("read_page", {"path": "wiki/concepts/ml.md"})
        v = check_pre_dispatch(
            "update_frontmatter",
            {"path": "wiki/concepts/ml.md", "fields": {"tags": ["ml"]}},
            ctx,
        )
        assert v.allowed

    def test_allowed_if_page_was_written_in_session(self) -> None:
        """If the agent created the page this session, it knows the content."""
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("find_existing_page", {"title": "New Topic"})
        ctx.record_tool_call("write_page", {"path": "wiki/concepts/new.md"})
        v = check_pre_dispatch(
            "append_section",
            {"path": "wiki/concepts/new.md", "heading": "Extra", "content": "..."},
            ctx,
        )
        assert v.allowed

    def test_structure_targets_exempt_from_read_before_write(self) -> None:
        """Structure writes don't need a prior read."""
        ctx = PolicyContext(attended=True)
        v = check_pre_dispatch(
            "append_section",
            {"path": "wiki/index.md", "heading": "New Hub", "content": "..."},
            ctx,
        )
        assert v.allowed

    def test_journal_targets_exempt(self) -> None:
        ctx = PolicyContext(attended=True)
        v = check_pre_dispatch(
            "append_section",
            {"path": "wiki/journals/2025-04-09.md", "heading": "Note", "content": "..."},
            ctx,
        )
        assert v.allowed


# ======================================================================
# Minimum note body length
# ======================================================================

def _make_note_content(body_len: int) -> str:
    """Build a note page with a body of approximately body_len chars."""
    fm = (
        "---\ntitle: Test Note\ntype: note\n"
        "summary: A test\ntags: [test]\n"
        "created: 2025-04-09\nupdated: 2025-04-09\n---\n\n"
    )
    body = "# Test Note\n\n" + ("x" * max(0, body_len - 14)) + "\n\n## Related\n"
    return fm + body


class TestNoteMinLength:
    def test_short_note_blocked(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("find_existing_page", {"title": "Test Note"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/concepts/test.md", "content": _make_note_content(50)},
            ctx,
        )
        assert not v.allowed
        assert str(MIN_NOTE_BODY_CHARS) in (v.warning or "")

    def test_long_enough_note_allowed(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("find_existing_page", {"title": "Test Note"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/concepts/test.md", "content": _make_note_content(250)},
            ctx,
        )
        assert v.allowed

    def test_exact_threshold_allowed(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("find_existing_page", {"title": "Test Note"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/concepts/test.md", "content": _make_note_content(MIN_NOTE_BODY_CHARS)},
            ctx,
        )
        assert v.allowed

    def test_canonical_not_affected_by_length_check(self) -> None:
        """Canonical has its own gate (sources), not length."""
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("find_existing_page", {"title": "Test"})
        content = (
            "---\ntitle: Test\ntype: canonical\n"
            "summary: A test\ntags: [test]\nsources: [textbook]\n"
            "created: 2025-04-09\nupdated: 2025-04-09\n---\n\n"
            "Short but has sources.\n"
        )
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/concepts/test.md", "content": content},
            ctx,
        )
        assert v.allowed

    def test_overwrite_existing_note_bypasses_length(self) -> None:
        """Overwriting a known page is always OK regardless of length."""
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("read_page", {"path": "wiki/concepts/existing.md"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/concepts/existing.md", "content": _make_note_content(50)},
            ctx,
        )
        assert v.allowed


# ======================================================================
# Synthesis link count
# ======================================================================

def _make_synthesis_content(link_count: int) -> str:
    fm = (
        "---\ntitle: Comparison\ntype: synthesis\n"
        "summary: Cross-topic analysis\ntags: [analysis]\n"
        "created: 2025-04-09\nupdated: 2025-04-09\n---\n\n"
    )
    links = " ".join(f"[[Topic {i}]]" for i in range(link_count))
    body = f"# Comparison\n\nThis analysis covers {links}.\n\n## Related\n"
    return fm + body


class TestSynthesisLinkCount:
    def test_synthesis_with_no_links_blocked(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("find_existing_page", {"title": "Comparison"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/synthesis/comparison.md", "content": _make_synthesis_content(0)},
            ctx,
        )
        assert not v.allowed
        assert "wiki-links" in (v.warning or "").lower()

    def test_synthesis_with_one_link_blocked(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("find_existing_page", {"title": "Comparison"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/synthesis/comparison.md", "content": _make_synthesis_content(1)},
            ctx,
        )
        assert not v.allowed

    def test_synthesis_with_two_links_allowed(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("find_existing_page", {"title": "Comparison"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/synthesis/comparison.md", "content": _make_synthesis_content(2)},
            ctx,
        )
        assert v.allowed

    def test_synthesis_with_many_links_allowed(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("find_existing_page", {"title": "Comparison"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/synthesis/comparison.md", "content": _make_synthesis_content(5)},
            ctx,
        )
        assert v.allowed

    def test_overwrite_existing_synthesis_bypasses_link_check(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("read_page", {"path": "wiki/synthesis/old.md"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/synthesis/old.md", "content": _make_synthesis_content(0)},
            ctx,
        )
        assert v.allowed


# ======================================================================
# Preferences.md gate
# ======================================================================

class TestPreferencesGate:
    def test_prefs_blocked_by_default(self) -> None:
        ctx = PolicyContext(attended=True)
        v = check_pre_dispatch(
            "write_page",
            {"path": ".schema/preferences.md", "content": "..."},
            ctx,
        )
        assert not v.allowed
        assert "preferences" in (v.warning or "").lower()

    def test_prefs_allowed_when_user_requested(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.user_requested_prefs_edit = True
        v = check_pre_dispatch(
            "write_page",
            {"path": ".schema/preferences.md", "content": "..."},
            ctx,
        )
        # write_page still goes through dedup check, but prefs gate passes
        # For prefs, dedup doesn't apply (it's a known file), so let's
        # also mark it as read
        ctx.record_tool_call("read_page", {"path": ".schema/preferences.md"})
        v = check_pre_dispatch(
            "write_page",
            {"path": ".schema/preferences.md", "content": "..."},
            ctx,
        )
        assert v.allowed

    def test_prefs_append_blocked_without_request(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("read_page", {"path": ".schema/preferences.md"})
        v = check_pre_dispatch(
            "append_section",
            {"path": ".schema/preferences.md", "heading": "New", "content": "..."},
            ctx,
        )
        assert not v.allowed

    def test_prefs_append_allowed_with_request(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.user_requested_prefs_edit = True
        ctx.record_tool_call("read_page", {"path": ".schema/preferences.md"})
        v = check_pre_dispatch(
            "append_section",
            {"path": ".schema/preferences.md", "heading": "New", "content": "..."},
            ctx,
        )
        assert v.allowed

    def test_unattended_blocks_prefs_even_with_flag(self) -> None:
        ctx = PolicyContext(attended=False)
        ctx.user_requested_prefs_edit = True
        v = check_pre_dispatch(
            "write_page",
            {"path": ".schema/preferences.md", "content": "..."},
            ctx,
        )
        assert not v.allowed


# ======================================================================
# Helper tests
# ======================================================================

class TestHelpers:
    def test_extract_type_note(self) -> None:
        assert _extract_type(_make_note_content(100)) == "note"

    def test_extract_type_synthesis(self) -> None:
        assert _extract_type(_make_synthesis_content(2)) == "synthesis"

    def test_extract_type_empty(self) -> None:
        assert _extract_type("no frontmatter here") == ""

    def test_strip_frontmatter(self) -> None:
        content = "---\ntitle: Test\ntype: note\n---\n\n# Body\n"
        body = _strip_frontmatter(content)
        assert body.startswith("# Body")
        assert "---" not in body

    def test_wiki_link_regex(self) -> None:
        text = "See [[Attention]] and [[Transformer Architecture]] for details."
        matches = _WIKI_LINK_RE.findall(text)
        assert len(matches) == 2
        assert "Attention" in matches
        assert "Transformer Architecture" in matches
