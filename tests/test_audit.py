"""Tests for vault audit and session organize features."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from noteweaver.adapters.provider import CompletionResult, ToolCall
from noteweaver.agent import KnowledgeAgent
from noteweaver.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


@pytest.fixture
def agent(vault: Vault) -> KnowledgeAgent:
    mock_provider = MagicMock()
    return KnowledgeAgent(vault=vault, provider=mock_provider)


def _page(title: str, ptype: str = "note", tags: list | None = None,
          summary: str = "", sources: str = "", extra: str = "",
          related: str = "") -> str:
    tags_str = ", ".join(tags) if tags else ""
    sources_line = f"sources: [{sources}]\n" if sources else ""
    return (
        f"---\ntitle: {title}\ntype: {ptype}\n"
        f"summary: {summary}\ntags: [{tags_str}]\n"
        f"{sources_line}"
        f"created: 2025-01-01\nupdated: 2025-01-01\n---\n\n"
        f"# {title}\n\n{extra}\n\n## Related\n{related}\n"
    )


# ======================================================================
# Vault Audit
# ======================================================================


class TestAuditVault:
    def test_empty_vault(self, vault: Vault) -> None:
        report = vault.audit_vault()
        assert "0 issues" in report["summary"]

    def test_stale_imports(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/stale.md",
            _page("Stale Import", tags=["imported"], summary="Imported from x.md"),
        )
        # write_file auto-sets updated to today; manually backdate to trigger >7 days
        import re as _re
        from datetime import datetime, timezone, timedelta
        path = vault._resolve("wiki/concepts/stale.md")
        content = path.read_text(encoding="utf-8")
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        content = _re.sub(r"updated: \d{4}-\d{2}-\d{2}", f"updated: {old_date}", content)
        path.write_text(content, encoding="utf-8")
        report = vault.audit_vault()
        assert len(report["stale_imports"]) == 1
        assert report["stale_imports"][0]["path"] == "wiki/concepts/stale.md"

    def test_recent_imports_not_stale(self, vault: Vault) -> None:
        """Imports updated within 7 days should not be flagged as stale."""
        vault.write_file(
            "wiki/concepts/fresh.md",
            _page("Fresh Import", tags=["imported"], summary="Imported from y.md"),
        )
        report = vault.audit_vault()
        assert len(report["stale_imports"]) == 0

    def test_orphan_pages(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/lonely.md", _page("Lonely Note"))
        report = vault.audit_vault()
        assert "wiki/concepts/lonely.md" in report["orphan_pages"]

    def test_hub_not_orphan(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/hub.md", _page("My Hub", ptype="hub"))
        report = vault.audit_vault()
        assert "wiki/concepts/hub.md" not in report.get("orphan_pages", [])

    def test_missing_summaries(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/no-sum.md", _page("No Summary"))
        report = vault.audit_vault()
        assert "wiki/concepts/no-sum.md" in report["missing_summaries"]

    def test_missing_summary_imported(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/imp.md",
            _page("Imp", summary="Imported from file.md"),
        )
        report = vault.audit_vault()
        assert "wiki/concepts/imp.md" in report["missing_summaries"]

    def test_good_summary_not_flagged(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/good.md",
            _page("Good", summary="A real summary of the page"),
        )
        report = vault.audit_vault()
        assert "wiki/concepts/good.md" not in report.get("missing_summaries", [])

    def test_broken_links(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/linker.md",
            _page("Linker", related="- [[Nonexistent Page]]"),
        )
        report = vault.audit_vault()
        assert len(report["broken_links"]) >= 1
        assert any(
            bl["link_title"] == "Nonexistent Page"
            for bl in report["broken_links"]
        )

    def test_valid_links_not_flagged(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("PageA", related="- [[PageB]]"))
        vault.write_file("wiki/concepts/b.md", _page("PageB", related="- [[PageA]]"))
        report = vault.audit_vault()
        broken_titles = {bl["link_title"] for bl in report.get("broken_links", [])}
        assert "PageA" not in broken_titles
        assert "PageB" not in broken_titles

    def test_hub_candidates(self, vault: Vault) -> None:
        for i in range(3):
            vault.write_file(
                f"wiki/concepts/ml-{i}.md",
                _page(f"ML Page {i}", tags=["machine-learning"]),
            )
        report = vault.audit_vault()
        assert len(report["hub_candidates"]) >= 1
        assert any(
            hc["tag"] == "machine-learning"
            for hc in report["hub_candidates"]
        )

    def test_hub_suppresses_candidate(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/ml-hub.md",
            _page("ML Hub", ptype="hub", tags=["ml"]),
        )
        for i in range(3):
            vault.write_file(
                f"wiki/concepts/ml-{i}.md",
                _page(f"ML Page {i}", tags=["ml"]),
            )
        report = vault.audit_vault()
        assert not any(
            hc["tag"] == "ml" for hc in report.get("hub_candidates", [])
        )

    def test_missing_connections(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/a.md",
            _page("A", tags=["topic-x", "topic-y"]),
        )
        vault.write_file(
            "wiki/concepts/b.md",
            _page("B", tags=["topic-x", "topic-y"]),
        )
        report = vault.audit_vault()
        assert len(report["missing_connections"]) >= 1

    def test_summary_format(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/x.md", _page("X"))
        vault.write_file("wiki/concepts/y.md", _page("Y", tags=["imported"]))
        report = vault.audit_vault()
        assert "issue(s) found" in report["summary"]


class TestAuditReport:
    def test_save_and_load(self, vault: Vault) -> None:
        report = vault.audit_vault()
        path = vault.save_audit_report(report)
        assert path.is_file()
        loaded = json.loads(path.read_text())
        assert loaded["summary"] == report["summary"]


class TestDaysSince:
    def test_same_day(self) -> None:
        assert Vault._days_since("2025-04-10", "2025-04-10") == 0

    def test_seven_days(self) -> None:
        assert Vault._days_since("2025-04-03", "2025-04-10") == 7

    def test_invalid_date(self) -> None:
        assert Vault._days_since("bad", "2025-04-10") is None


# ======================================================================
# scan_vault_context
# ======================================================================


class TestScanVaultContext:
    def test_empty_vault(self, vault: Vault) -> None:
        ctx = vault.scan_vault_context()
        assert "Total: 0 pages" in ctx

    def test_with_hub_and_pages(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/ml.md",
            _page("ML", ptype="hub", tags=["ml"]),
        )
        vault.write_file(
            "wiki/concepts/dl.md",
            _page("Deep Learning", tags=["ml", "dl"], summary="DL overview"),
        )
        ctx = vault.scan_vault_context()
        assert "ML (1 pages)" in ctx
        assert "wiki/concepts/ml.md" in ctx
        assert "ml" in ctx
        assert "dl" in ctx
        # Small vault → full tier shows child pages with summaries
        assert "Deep Learning" in ctx
        assert "DL overview" in ctx

    def test_hub_shows_page_count(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/react-hub.md",
            _page("React", ptype="hub", tags=["react"]),
        )
        for i in range(5):
            vault.write_file(
                f"wiki/concepts/react-{i}.md",
                _page(f"React Page {i}", tags=["react"]),
            )
        ctx = vault.scan_vault_context()
        assert "React (5 pages)" in ctx
        assert "wiki/concepts/react-hub.md" in ctx
        assert "Total: 6 pages" in ctx
        # Hub children listed
        assert "React Page 0" in ctx

    def test_unorganized_listed(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/orphan.md",
            _page("Orphan", tags=["misc"]),
        )
        ctx = vault.scan_vault_context()
        assert "Unorganized" in ctx
        assert "1 page(s)" in ctx
        assert "Orphan" in ctx

    def test_compact_tier_no_summaries(self, vault: Vault) -> None:
        """Medium vault (40-150 pages) uses compact tier: titles but no summaries."""
        vault.write_file("wiki/concepts/hub.md", _page("Hub", ptype="hub", tags=["t"]))
        for i in range(45):
            vault.write_file(
                f"wiki/concepts/p{i}.md",
                _page(f"Page {i}", tags=["t"], summary=f"Summary {i}"),
            )
        ctx = vault.scan_vault_context()
        assert "Page 0" in ctx
        assert "Summary 0" not in ctx
        # Paths shown in compact mode
        assert "wiki/concepts/p0.md" in ctx

    def test_large_tier_truncated(self, vault: Vault) -> None:
        """Large vault (150+) truncates hub member lists."""
        vault.write_file("wiki/concepts/hub.md", _page("Hub", ptype="hub", tags=["t"]))
        for i in range(160):
            vault.write_file(f"wiki/concepts/p{i}.md", _page(f"Page {i}", tags=["t"]))
        ctx = vault.scan_vault_context()
        assert "… and " in ctx
        assert " more" in ctx

    def test_journal_range_shown(self, vault: Vault) -> None:
        """Journal entries produce a date range in the output."""
        vault.write_file(
            "wiki/journals/2025-03-01.md",
            _page("2025-03-01", ptype="journal"),
        )
        vault.write_file(
            "wiki/journals/2025-04-10.md",
            _page("2025-04-10", ptype="journal"),
        )
        ctx = vault.scan_vault_context()
        assert "Journals: 2 entries" in ctx
        assert "2025-03-01" in ctx
        assert "2025-04-10" in ctx

    def test_journals_excluded_from_total(self, vault: Vault) -> None:
        """Journal pages are not counted in the 'Total: N pages' line."""
        vault.write_file("wiki/concepts/a.md", _page("A"))
        vault.write_file("wiki/journals/2025-01-01.md", _page("J", ptype="journal"))
        ctx = vault.scan_vault_context()
        assert "Total: 1 pages" in ctx

    def test_vault_context_injected_into_prompt(self, vault: Vault, agent: KnowledgeAgent) -> None:
        vault.write_file(
            "wiki/concepts/ml.md",
            _page("ML", ptype="hub", tags=["ml"], summary="Machine learning"),
        )
        query = agent._build_messages_for_query()
        system = query[0]["content"]
        assert "Current Vault Contents" in system
        assert "ML" in system

    def test_empty_vault_shows_welcome(self, vault: Vault, agent: KnowledgeAgent) -> None:
        query = agent._build_messages_for_query()
        system = query[0]["content"]
        assert "vault is empty" in system.lower()

    def test_footer_mentions_current_tools(self, vault: Vault) -> None:
        """The footer hint references tools that actually exist."""
        ctx = vault.scan_vault_context()
        assert "survey_topic" in ctx
        assert "list_pages" in ctx
        assert "search" in ctx
        # Old stale tool names must NOT appear
        assert "list_page_summaries" not in ctx
        assert "search_vault" not in ctx
        assert "find_existing_page" not in ctx


# ======================================================================
# Session Organize: conversation digest
# ======================================================================


class TestConversationDigest:
    def test_basic_digest(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "What is attention?"})
        agent.messages.append({"role": "assistant", "content": "Attention is a mechanism..."})
        digest = agent._build_conversation_digest()
        assert "What is attention" in digest
        assert "Attention is a mechanism" in digest

    def test_digest_respects_boundary(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "Old message"})
        agent.messages.append({"role": "assistant", "content": "Old reply"})
        agent._last_organize_boundary = 3
        agent.messages.append({"role": "user", "content": "New message"})
        digest = agent._build_conversation_digest()
        assert "New message" in digest
        assert "Old message" not in digest

    def test_digest_includes_tool_calls(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({
            "role": "assistant",
            "tool_calls": [{
                "id": "tc1", "type": "function",
                "function": {
                    "name": "read_page",
                    "arguments": json.dumps({"path": "wiki/concepts/test.md"}),
                },
            }],
        })
        digest = agent._build_conversation_digest()
        assert "read_page" in digest

    def test_empty_conversation(self, agent: KnowledgeAgent) -> None:
        digest = agent._build_conversation_digest()
        assert digest == ""


# ======================================================================
# Session Organize: should_organize
# ======================================================================


class TestShouldOrganize:
    def test_below_threshold(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "short"})
        assert not agent.should_organize()

    def test_above_threshold(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "x" * 2000})
        agent.messages.append({"role": "assistant", "content": "y" * 2000})
        assert agent.should_organize()

    def test_threshold_counts_both_roles(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "x" * 1000})
        agent.messages.append({"role": "assistant", "content": "y" * 1000})
        agent.messages.append({"role": "user", "content": "z" * 1500})
        assert agent.should_organize()


# ======================================================================
# Session Organize: generate / format / execute plan
# ======================================================================


class TestGenerateOrganizePlan:
    def test_returns_none_for_short_conversation(self, agent: KnowledgeAgent) -> None:
        plan = agent.generate_organize_plan()
        assert plan is None

    def test_returns_none_when_no_tool_calls(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "Let's discuss React"})
        agent.messages.append({"role": "assistant", "content": "React is great."})
        agent.messages.append({"role": "user", "content": "Tell me more about hooks."})
        agent.provider.chat_completion.return_value = (
            CompletionResult(content="Nothing to capture.", tool_calls=[]),
            {"role": "assistant", "content": "Nothing to capture."},
        )
        plan = agent.generate_organize_plan()
        assert plan is None

    def test_returns_plan_with_tool_calls(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "React hooks are useful for state"})
        agent.messages.append({"role": "assistant", "content": "Yes, especially useState."})
        agent.messages.append({"role": "user", "content": "And useEffect for side effects."})

        agent.provider.chat_completion.return_value = (
            CompletionResult(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="submit_plan",
                        arguments=json.dumps({
                            "summary": "Capture useState info to react-hooks page",
                            "targets": ["wiki/concepts/react-hooks.md"],
                            "rationale": "User discussed React hooks",
                            "intent": "append",
                            "change_type": "incremental",
                        }),
                    ),
                ],
            ),
            {"role": "assistant", "tool_calls": []},
        )

        plan = agent.generate_organize_plan()
        assert plan is not None
        from noteweaver.plan import Plan
        assert isinstance(plan, Plan)
        assert "react-hooks" in plan.targets[0]

    def test_plan_persisted_to_disk(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "Important content"})
        agent.messages.append({"role": "assistant", "content": "Noted."})
        agent.messages.append({"role": "user", "content": "More important stuff."})
        agent.provider.chat_completion.return_value = (
            CompletionResult(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="submit_plan",
                        arguments=json.dumps({
                            "summary": "Capture session note",
                            "rationale": "Important content discussed",
                            "intent": "create",
                            "change_type": "structural",
                        }),
                    ),
                ],
            ),
            {"role": "assistant", "tool_calls": []},
        )
        plan = agent.generate_organize_plan()
        assert plan is not None
        loaded = agent.plan_store.load(plan.id)
        assert loaded is not None
        assert loaded.id == plan.id

    def test_provider_error_returns_none(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "test"})
        agent.messages.append({"role": "assistant", "content": "reply"})
        agent.messages.append({"role": "user", "content": "more"})
        agent.provider.chat_completion.side_effect = Exception("API down")
        plan = agent.generate_organize_plan()
        assert plan is None


class TestFormatOrganizePlan:
    def test_format_legacy_write_page(self, agent: KnowledgeAgent) -> None:
        plan = [{"name": "write_page", "arguments": {"path": "wiki/concepts/test.md", "content": "..."}}]
        text = agent.format_organize_plan(plan)
        assert "write_page" in text
        assert "wiki/concepts/test.md" in text

    def test_format_legacy_capture_append(self, agent: KnowledgeAgent) -> None:
        plan = [{
            "name": "capture",
            "arguments": {
                "target": "wiki/concepts/x.md",
                "title": "New",
                "content": "body",
            },
        }]
        text = agent.format_organize_plan(plan)
        assert "capture" in text

    def test_format_legacy_update_frontmatter(self, agent: KnowledgeAgent) -> None:
        plan = [{
            "name": "organize",
            "arguments": {
                "target": "wiki/concepts/x.md",
                "action": "update_metadata",
                "metadata": {"tags": ["a"]},
            },
        }]
        text = agent.format_organize_plan(plan)
        assert "organize" in text

    def test_format_legacy_add_related_link(self, agent: KnowledgeAgent) -> None:
        plan = [{
            "name": "organize",
            "arguments": {
                "target": "wiki/concepts/x.md",
                "action": "link",
                "link_to": "Y",
            },
        }]
        text = agent.format_organize_plan(plan)
        assert "organize" in text

    def test_format_empty_plan(self, agent: KnowledgeAgent) -> None:
        assert agent.format_organize_plan([]) == ""

    def test_format_plan_object(self, agent: KnowledgeAgent) -> None:
        from noteweaver.plan import Plan, PlanStatus
        plan = Plan(
            id="plan-test",
            status=PlanStatus.PENDING,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            summary="Add React hooks notes",
            targets=["wiki/concepts/react.md"],
            rationale="User discussed hooks",
            intent="append",
            change_type="incremental",
        )
        text = agent.format_plan(plan)
        assert "追加内容" in text
        assert "react.md" in text


class TestExecuteOrganizePlan:
    def test_execute_appends_section(self, vault: Vault, agent: KnowledgeAgent) -> None:
        vault.write_file(
            "wiki/concepts/test.md",
            _page("Test", summary="A test page"),
        )
        # Policy requires the page to be read before editing
        agent._policy_ctx.pages_read.append("wiki/concepts/test.md")
        plan = [{
            "name": "capture",
            "arguments": {
                "target": "wiki/concepts/test.md",
                "title": "New Info",
                "content": "Some new content.",
            },
        }]
        result = agent.execute_organize_plan(plan)
        assert "1 成功" in result
        content = vault.read_file("wiki/concepts/test.md")
        assert "## New Info" in content

    def test_execute_updates_frontmatter(self, vault: Vault, agent: KnowledgeAgent) -> None:
        vault.write_file("wiki/concepts/test.md", _page("Test"))
        # Policy requires the page to be read before editing
        agent._policy_ctx.pages_read.append("wiki/concepts/test.md")
        plan = [{
            "name": "organize",
            "arguments": {
                "target": "wiki/concepts/test.md",
                "action": "update_metadata",
                "metadata": {"tags": ["updated-tag"]},
            },
        }]
        result = agent.execute_organize_plan(plan)
        assert "1 成功" in result
        from noteweaver.frontmatter import extract_frontmatter
        content = vault.read_file("wiki/concepts/test.md")
        fm = extract_frontmatter(content)
        assert "updated-tag" in fm["tags"]

    def test_execute_blocks_unread_page(self, vault: Vault, agent: KnowledgeAgent) -> None:
        """Policy blocks editing a page that wasn't read in this session."""
        vault.write_file("wiki/concepts/test.md", _page("Test", summary="A test page"))
        plan = [{
            "name": "organize",
            "arguments": {
                "target": "wiki/concepts/test.md",
                "action": "update_metadata",
                "metadata": {"tags": ["sneaky"]},
            },
        }]
        result = agent.execute_organize_plan(plan)
        assert "blocked by policy" in result

    def test_execute_handles_errors(self, vault: Vault, agent: KnowledgeAgent) -> None:
        plan = [{
            "name": "read_page",
            "arguments": {"path": "wiki/concepts/nonexistent.md"},
        }]
        result = agent.execute_organize_plan(plan)
        assert "1 项操作" in result

    def test_execute_loads_from_disk(self, vault: Vault, agent: KnowledgeAgent) -> None:
        vault.write_file("wiki/concepts/test.md", _page("Test", summary="A test page"))
        vault.write_file("wiki/concepts/other.md", _page("Other", summary="Linked page"))
        agent._policy_ctx.pages_read.append("wiki/concepts/test.md")
        plan = [{
            "name": "organize",
            "arguments": {
                "target": "wiki/concepts/test.md",
                "action": "link",
                "link_to": "Other",
            },
        }]
        agent._save_pending_plan(plan)
        result = agent.execute_organize_plan()
        assert "1 成功" in result
        assert agent._load_pending_plan() is None

    def test_execute_empty_plan(self, agent: KnowledgeAgent) -> None:
        result = agent.execute_organize_plan()
        assert "没有" in result

    def test_execute_advances_boundary(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "test"})
        agent.messages.append({"role": "assistant", "content": "reply"})
        plan = [{
            "name": "restructure",
            "arguments": {"scope": "vault", "action": "audit"},
        }]
        old_boundary = agent._last_organize_boundary
        agent.execute_organize_plan(plan)
        assert agent._last_organize_boundary > old_boundary

    def test_multiple_actions(self, vault: Vault, agent: KnowledgeAgent) -> None:
        vault.write_file("wiki/concepts/a.md", _page("A", summary="Page A"))
        vault.write_file("wiki/concepts/b.md", _page("B", summary="Page B"))
        agent._policy_ctx.pages_read.extend(["wiki/concepts/a.md", "wiki/concepts/b.md"])
        plan = [
            {
                "name": "organize",
                "arguments": {
                    "target": "wiki/concepts/a.md",
                    "action": "link",
                    "link_to": "B",
                },
            },
            {
                "name": "organize",
                "arguments": {
                    "target": "wiki/concepts/b.md",
                    "action": "link",
                    "link_to": "A",
                },
            },
        ]
        result = agent.execute_organize_plan(plan)
        assert "2 项操作" in result
        assert "2 成功" in result


# ======================================================================
# Pending plan storage
# ======================================================================


class TestPendingPlan:
    def test_save_load_clear(self, agent: KnowledgeAgent) -> None:
        plan = [{"name": "read_page", "arguments": {"path": "x.md"}}]
        agent._save_pending_plan(plan)
        loaded = agent._load_pending_plan()
        assert loaded == plan
        agent._clear_pending_plan()
        assert agent._load_pending_plan() is None

    def test_load_nonexistent(self, agent: KnowledgeAgent) -> None:
        assert agent._load_pending_plan() is None

    def test_clear_nonexistent(self, agent: KnowledgeAgent) -> None:
        agent._clear_pending_plan()


# ======================================================================
# Audit injection into system prompt
# ======================================================================


class TestAuditInjection:
    def test_no_audit_file(self, agent: KnowledgeAgent) -> None:
        query = agent._build_messages_for_query()
        system = query[0]["content"]
        assert "Vault Health" not in system

    def test_audit_with_issues(self, vault: Vault, agent: KnowledgeAgent) -> None:
        report = {"summary": "3 issue(s) found: 1 orphan, 2 missing summaries"}
        vault.save_audit_report(report)
        query = agent._build_messages_for_query()
        system = query[0]["content"]
        assert "Vault Health" in system
        assert "3 issue" in system

    def test_audit_no_issues_not_injected(self, vault: Vault, agent: KnowledgeAgent) -> None:
        report = {"summary": "0 issues found"}
        vault.save_audit_report(report)
        query = agent._build_messages_for_query()
        system = query[0]["content"]
        assert "Vault Health" not in system


# ======================================================================
# Write interception in chat()
# ======================================================================


class TestWriteInterception:
    def test_read_tools_execute_normally(self, vault: Vault) -> None:
        from noteweaver.adapters.provider import CompletionResult, ToolCall
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            (CompletionResult(content=None, tool_calls=[
                ToolCall(id="tc1", name="read_page",
                         arguments=json.dumps({"path": "wiki/index.md"})),
            ]), {"role": "assistant", "tool_calls": [
                {"id": "tc1", "type": "function",
                 "function": {"name": "read_page", "arguments": json.dumps({"path": "wiki/index.md"})}}
            ]}),
            (CompletionResult(content="The vault has an index."), {"role": "assistant", "content": "The vault has an index."}),
        ]
        agent = KnowledgeAgent(vault=vault, provider=provider)
        responses = list(agent.chat("What's in the vault?"))
        assert any("read_page" in r for r in responses)
        assert any("index" in r.lower() for r in responses)
        assert len(agent.plan_store.list_pending()) == 0

    def test_write_page_runs_during_chat(self, vault: Vault) -> None:
        """V2: write tools run in chat (no submit_plan / pending plans)."""
        new_path = "wiki/concepts/test.md"
        new_content = _page("Test Page", tags=["note"], summary="From chat")
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            (CompletionResult(content=None, tool_calls=[
                ToolCall(
                    id="tc1",
                    name="list_pages",
                    arguments=json.dumps({"directory": "wiki/concepts"}),
                ),
            ]), {"role": "assistant", "tool_calls": [
                {"id": "tc1", "type": "function",
                 "function": {
                     "name": "list_pages",
                     "arguments": json.dumps({"directory": "wiki/concepts"}),
                 }},
            ]}),
            (CompletionResult(content=None, tool_calls=[
                ToolCall(
                    id="tc2",
                    name="write_page",
                    arguments=json.dumps({"path": new_path, "content": new_content}),
                ),
            ]), {"role": "assistant", "tool_calls": [
                {"id": "tc2", "type": "function",
                 "function": {
                     "name": "write_page",
                     "arguments": json.dumps({"path": new_path, "content": new_content}),
                 }},
            ]}),
            (CompletionResult(content="Created the test page."),
             {"role": "assistant", "content": "Created the test page."}),
        ]
        agent = KnowledgeAgent(vault=vault, provider=provider)
        responses = list(agent.chat("Create a test page"))
        assert any("↳ write_page" in r for r in responses)
        assert (vault.root / new_path).is_file()
        assert "Test Page" in vault.read_file(new_path)
        assert len(agent.plan_store.list_pending()) == 0

    def test_mixed_read_and_write_in_session(self, vault: Vault) -> None:
        """V2: same session can read then write without a plan step."""
        new_path = "wiki/concepts/chat-note.md"
        new_content = _page("Chat Note", tags=["note"], summary="After read")
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            (CompletionResult(content=None, tool_calls=[
                ToolCall(id="tc1", name="read_page",
                         arguments=json.dumps({"path": "wiki/index.md"})),
            ]), {"role": "assistant", "tool_calls": [
                {"id": "tc1", "type": "function",
                 "function": {"name": "read_page", "arguments": json.dumps({"path": "wiki/index.md"})}}
            ]}),
            (CompletionResult(content=None, tool_calls=[
                ToolCall(
                    id="tc2",
                    name="write_page",
                    arguments=json.dumps({"path": new_path, "content": new_content}),
                ),
            ]), {"role": "assistant", "tool_calls": [
                {"id": "tc2", "type": "function",
                 "function": {
                     "name": "write_page",
                     "arguments": json.dumps({"path": new_path, "content": new_content}),
                 }},
            ]}),
            (CompletionResult(content="Done."), {"role": "assistant", "content": "Done."}),
        ]
        agent = KnowledgeAgent(vault=vault, provider=provider)
        responses = list(agent.chat("Do something"))
        assert any("↳ read_page" in r for r in responses)
        assert any("↳ write_page" in r for r in responses)
        assert (vault.root / new_path).is_file()
        assert len(agent.plan_store.list_pending()) == 0

    def test_no_writes_no_plan(self, vault: Vault) -> None:
        from noteweaver.adapters.provider import CompletionResult
        provider = MagicMock()
        provider.chat_completion.return_value = (
            CompletionResult(content="Just a chat."),
            {"role": "assistant", "content": "Just a chat."},
        )
        agent = KnowledgeAgent(vault=vault, provider=provider)
        list(agent.chat("Hello"))
        assert len(agent.plan_store.list_pending()) == 0


class TestToolSchemas:
    def test_chat_schemas_match_full_tool_set(self) -> None:
        """V2: CHAT_TOOL_SCHEMAS is the same as TOOL_SCHEMAS (read + write in chat)."""
        from noteweaver.tools.definitions import (
            CHAT_TOOL_SCHEMAS,
            TOOL_SCHEMAS,
            _OBSERVATION_TOOL_NAMES,
        )
        chat_names = {s["function"]["name"] for s in CHAT_TOOL_SCHEMAS}
        full_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert chat_names == full_names
        assert _OBSERVATION_TOOL_NAMES.issubset(chat_names)
        for name in ("read_page", "write_page", "append_section",
                     "update_frontmatter", "add_related_link"):
            assert name in chat_names

    def test_full_schemas_contain_write_tools(self) -> None:
        from noteweaver.tools.definitions import TOOL_SCHEMAS
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        for tool in ["write_page", "append_section", "update_frontmatter", "add_related_link"]:
            assert tool in names, f"{tool} should be in TOOL_SCHEMAS"


# ======================================================================
# Progressive disclosure enforcement
# ======================================================================


class TestProgressiveDisclosure:
    def test_orphan_page_linked_to_hub(self, vault: Vault, agent: KnowledgeAgent) -> None:
        """When a page is created with a tag matching a hub, it gets linked."""
        vault.write_file(
            "wiki/concepts/ml-hub.md",
            _page("ML", ptype="hub", tags=["ml"], summary="ML overview"),
        )
        vault.write_file(
            "wiki/concepts/new-page.md",
            _page("New ML Page", tags=["ml"], summary="A new ML page"),
        )
        plan = [{"name": "write_page", "arguments": {
            "path": "wiki/concepts/new-page.md",
            "content": _page("New ML Page", tags=["ml"], summary="A new ML page"),
        }}]
        report = agent._ensure_progressive_disclosure(plan)
        assert any("链接" in r for r in report)
        hub_content = vault.read_file("wiki/concepts/ml-hub.md")
        assert "[[New ML Page]]" in hub_content

    def test_orphan_page_creates_hub_when_no_hub_exists(
        self, vault: Vault, agent: KnowledgeAgent,
    ) -> None:
        """When no hub exists for a tag, a new hub is created for the orphan page."""
        vault.write_file(
            "wiki/concepts/solo.md",
            _page("Solo Page", tags=["niche"], summary="A solo page"),
        )
        plan = [{"name": "write_page", "arguments": {
            "path": "wiki/concepts/solo.md",
            "content": _page("Solo Page", tags=["niche"], summary="A solo page"),
        }}]
        report = agent._ensure_progressive_disclosure(plan)
        assert any("hub" in r.lower() for r in report)
        hub_content = vault.read_file("wiki/concepts/niche.md")
        assert "[[Solo Page]]" in hub_content

    def test_already_linked_page_no_action(self, vault: Vault, agent: KnowledgeAgent) -> None:
        """A page that already has inbound links needs no disclosure fix."""
        vault.write_file("wiki/concepts/a.md", _page("A", related="- [[B]]"))
        vault.write_file("wiki/concepts/b.md", _page("B", related="- [[A]]"))
        plan = [{"name": "capture", "arguments": {
            "target": "wiki/concepts/a.md",
            "title": "New",
            "content": "x",
        }}]
        report = agent._ensure_progressive_disclosure(plan)
        assert len(report) == 0


# ======================================================================
# Tag normalization
# ======================================================================


class TestTagNormalization:
    def test_lowercase(self) -> None:
        assert Vault.normalize_tag("ML") == "ml"
        assert Vault.normalize_tag("React") == "react"

    def test_spaces_to_hyphens(self) -> None:
        assert Vault.normalize_tag("machine learning") == "machine-learning"

    def test_underscores_to_hyphens(self) -> None:
        assert Vault.normalize_tag("deep_learning") == "deep-learning"

    def test_strips_special_chars(self) -> None:
        assert Vault.normalize_tag("c++") == "c"
        assert Vault.normalize_tag("node.js") == "nodejs"

    def test_preserves_cjk(self) -> None:
        assert Vault.normalize_tag("机器学习") == "机器学习"
        assert Vault.normalize_tag("React 入门") == "react-入门"

    def test_collapses_hyphens(self) -> None:
        assert Vault.normalize_tag("a--b---c") == "a-b-c"

    def test_numeric_tag(self) -> None:
        assert Vault.normalize_tag(2026) == "2026"
        assert Vault.normalize_tag(3.14) == "314"

    def test_write_normalizes_tags(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/test.md",
            "---\ntitle: Test\ntype: note\ntags: [Machine Learning, deep_learning, ML]\n---\n# T",
        )
        from noteweaver.frontmatter import extract_frontmatter
        content = vault.read_file("wiki/concepts/test.md")
        fm = extract_frontmatter(content)
        assert fm["tags"] == ["machine-learning", "deep-learning", "ml"]

    def test_deduplicates_after_normalize(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/test.md",
            "---\ntitle: Test\ntype: note\ntags: [ML, ml, Ml]\n---\n# T",
        )
        from noteweaver.frontmatter import extract_frontmatter
        content = vault.read_file("wiki/concepts/test.md")
        fm = extract_frontmatter(content)
        assert fm["tags"] == ["ml"]


# ======================================================================
# Stale import hint in list_pages
# ======================================================================


# ======================================================================
# read_page title resolution
# ======================================================================


class TestReadPageByTitle:
    def test_read_by_path(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        result = dispatch_tool(vault, "read_page", {"path": "wiki/index.md"})
        assert "Wiki Index" in result

    def test_read_by_title(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        vault.write_file("wiki/concepts/test.md", _page("My Test Page", summary="A test"))
        result = dispatch_tool(vault, "read_page", {"path": "My Test Page"})
        assert "My Test Page" in result

    def test_read_by_title_case_insensitive(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        vault.write_file("wiki/concepts/test.md", _page("Neural Networks", summary="NNs"))
        result = dispatch_tool(vault, "read_page", {"path": "neural networks"})
        assert "Neural Networks" in result

    def test_read_by_title_not_found(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        result = dispatch_tool(vault, "read_page", {"path": "Nonexistent Page"})
        assert "Error" in result
        assert "search" in result or "list_pages" in result or "No page" in result

    def test_path_takes_priority(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        vault.write_file("wiki/concepts/test.md", _page("Test", summary="Content"))
        result = dispatch_tool(vault, "read_page", {"path": "wiki/concepts/test.md"})
        assert "Test" in result


# ======================================================================
# Title uniqueness
# ======================================================================


class TestTitleUniqueness:
    def test_duplicate_title_rejected(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("Unique Title"))
        with pytest.raises(PermissionError, match="already used"):
            vault.write_file("wiki/concepts/b.md", _page("Unique Title"))

    def test_overwrite_same_file_ok(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("Title A"))
        vault.write_file("wiki/concepts/a.md", _page("Title A", summary="updated"))

    def test_different_titles_ok(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("Title A"))
        vault.write_file("wiki/concepts/b.md", _page("Title B"))

    def test_archive_exempt(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/old.md", _page("Old Page"))
        vault.write_file("wiki/archive/old.md", _page("Old Page"))

    def test_resolve_title(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/test.md", _page("Find Me"))
        assert vault.resolve_title("Find Me") == "wiki/concepts/test.md"
        assert vault.resolve_title("find me") == "wiki/concepts/test.md"
        assert vault.resolve_title("Nonexistent") is None


# ======================================================================
# restructure(action='merge_tags')
# ======================================================================


class TestMergeTags:
    def test_basic_merge(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        vault.write_file("wiki/concepts/a.md", _page("A", tags=["ml"]))
        vault.write_file("wiki/concepts/b.md", _page("B", tags=["ml", "dl"]))
        vault.write_file("wiki/concepts/c.md", _page("C", tags=["dl"]))
        result = dispatch_tool(
            vault,
            "restructure",
            {
                "scope": "vault",
                "action": "merge_tags",
                "old_tag": "ml",
                "new_tag": "machine-learning",
            },
        )
        assert "2 file(s)" in result
        from noteweaver.frontmatter import extract_frontmatter
        a = extract_frontmatter(vault.read_file("wiki/concepts/a.md"))
        assert "machine-learning" in a["tags"]
        assert "ml" not in a["tags"]
        c = extract_frontmatter(vault.read_file("wiki/concepts/c.md"))
        assert c["tags"] == ["dl"]

    def test_merge_deduplicates(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        vault.write_file("wiki/concepts/a.md", _page("A", tags=["ml", "machine-learning"]))
        result = dispatch_tool(
            vault,
            "restructure",
            {
                "scope": "vault",
                "action": "merge_tags",
                "old_tag": "ml",
                "new_tag": "machine-learning",
            },
        )
        assert "1 file(s)" in result
        from noteweaver.frontmatter import extract_frontmatter
        a = extract_frontmatter(vault.read_file("wiki/concepts/a.md"))
        assert a["tags"] == ["machine-learning"]

    def test_merge_nonexistent_tag(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        result = dispatch_tool(
            vault,
            "restructure",
            {
                "scope": "vault",
                "action": "merge_tags",
                "old_tag": "nope",
                "new_tag": "something",
            },
        )
        assert "No pages" in result

    def test_merge_same_tag(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        result = dispatch_tool(
            vault,
            "restructure",
            {
                "scope": "vault",
                "action": "merge_tags",
                "old_tag": "ml",
                "new_tag": "ML",
            },
        )
        assert "already the same" in result


# ======================================================================
# Audit: similar tags detection
# ======================================================================


class TestAuditSimilarTags:
    def test_substring_detected(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("A", tags=["react"]))
        vault.write_file("wiki/concepts/b.md", _page("B", tags=["react-native"]))
        report = vault.audit_vault()
        assert any(
            ("react" in st.get("tag_a", "") or "react" in st.get("tag_b", ""))
            and "substring" in st.get("reason", "")
            for st in report.get("similar_tags", [])
        )

    def test_edit_distance_detected(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("A", tags=["react"]))
        vault.write_file("wiki/concepts/b.md", _page("B", tags=["reactjs"]))
        report = vault.audit_vault()
        assert any(
            "react" in st.get("tag_a", "") and "reactjs" in st.get("tag_b", "")
            or "reactjs" in st.get("tag_a", "") and "react" in st.get("tag_b", "")
            for st in report.get("similar_tags", [])
        )

    def test_no_false_positives(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("A", tags=["python"]))
        vault.write_file("wiki/concepts/b.md", _page("B", tags=["javascript"]))
        report = vault.audit_vault()
        assert len(report.get("similar_tags", [])) == 0


class TestEditDistance:
    def test_identical(self) -> None:
        assert Vault._edit_distance("abc", "abc") == 0

    def test_one_char_diff(self) -> None:
        assert Vault._edit_distance("react", "reactx") == 1

    def test_two_char_diff(self) -> None:
        assert Vault._edit_distance("react", "reactjs") == 2

    def test_completely_different(self) -> None:
        assert Vault._edit_distance("abc", "xyz") == 3


class TestStaleImportHint:
    def test_no_imported_no_hint(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        vault.write_file("wiki/concepts/clean.md", _page("Clean", tags=["ml"]))
        result = dispatch_tool(vault, "list_pages", {"directory": "wiki/concepts"})
        assert "still tagged [imported]" not in result

    def test_imported_tag_listed(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        vault.write_file("wiki/concepts/imp.md", _page("Imp", tags=["imported"]))
        result = dispatch_tool(vault, "list_pages", {"directory": "wiki/concepts"})
        assert "imported" in result
        assert "Imp" in result


# ======================================================================
# Title uniqueness: rejection includes actionable guidance
# ======================================================================


class TestTitleUniquenessGuidance:
    def test_rejection_suggests_read_page(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("Attention Mechanism"))
        with pytest.raises(PermissionError, match="read_page"):
            vault.write_file("wiki/concepts/b.md", _page("Attention Mechanism"))

    def test_rejection_suggests_append(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("Attention Mechanism"))
        with pytest.raises(PermissionError, match="update it with"):
            vault.write_file("wiki/concepts/b.md", _page("Attention Mechanism"))

    def test_rejection_mentions_existing_path(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("Attention Mechanism"))
        with pytest.raises(PermissionError, match="wiki/concepts/a.md"):
            vault.write_file("wiki/concepts/b.md", _page("Attention Mechanism"))

    def test_rejection_via_write_page_handler(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        vault.write_file("wiki/concepts/a.md", _page("Dup Title"))
        result = dispatch_tool(vault, "write_page", {
            "path": "wiki/concepts/b.md",
            "content": _page("Dup Title"),
        })
        assert "Error" in result
        assert "read_page" in result
        assert "append_section" in result or "capture" in result

    def test_case_insensitive_rejection(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("React Hooks"))
        with pytest.raises(PermissionError, match="already used"):
            vault.write_file("wiki/concepts/b.md", _page("react hooks"))


# ======================================================================
# restructure(merge_tags) interception and format_organize_plan
# ======================================================================


class TestMergeTagsInterception:
    def test_tag_merge_uses_update_frontmatter_in_chat(self, vault: Vault) -> None:
        """V2: tag renames use write tools in chat (no submit_plan)."""
        vault.write_file(
            "wiki/concepts/one.md",
            _page("One", tags=["ml"], summary="First"),
        )
        vault.write_file(
            "wiki/concepts/two.md",
            _page("Two", tags=["ml"], summary="Second"),
        )
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            (CompletionResult(content=None, tool_calls=[
                ToolCall(
                    id="tc1",
                    name="list_pages",
                    arguments=json.dumps({"directory": "wiki/concepts"}),
                ),
            ]), {"role": "assistant", "tool_calls": [
                {"id": "tc1", "type": "function",
                 "function": {
                     "name": "list_pages",
                     "arguments": json.dumps({"directory": "wiki/concepts"}),
                 }},
            ]}),
            (CompletionResult(content=None, tool_calls=[
                ToolCall(
                    id="tc2",
                    name="read_page",
                    arguments=json.dumps({"path": "wiki/concepts/one.md"}),
                ),
                ToolCall(
                    id="tc3",
                    name="read_page",
                    arguments=json.dumps({"path": "wiki/concepts/two.md"}),
                ),
            ]), {"role": "assistant", "tool_calls": [
                {"id": "tc2", "type": "function",
                 "function": {
                     "name": "read_page",
                     "arguments": json.dumps({"path": "wiki/concepts/one.md"}),
                 }},
                {"id": "tc3", "type": "function",
                 "function": {
                     "name": "read_page",
                     "arguments": json.dumps({"path": "wiki/concepts/two.md"}),
                 }},
            ]}),
            (CompletionResult(content=None, tool_calls=[
                ToolCall(
                    id="tc4",
                    name="update_frontmatter",
                    arguments=json.dumps({
                        "path": "wiki/concepts/one.md",
                        "fields": {"tags": ["machine-learning"]},
                    }),
                ),
                ToolCall(
                    id="tc5",
                    name="update_frontmatter",
                    arguments=json.dumps({
                        "path": "wiki/concepts/two.md",
                        "fields": {"tags": ["machine-learning"]},
                    }),
                ),
            ]), {"role": "assistant", "tool_calls": [
                {"id": "tc4", "type": "function",
                 "function": {
                     "name": "update_frontmatter",
                     "arguments": json.dumps({
                         "path": "wiki/concepts/one.md",
                         "fields": {"tags": ["machine-learning"]},
                     }),
                 }},
                {"id": "tc5", "type": "function",
                 "function": {
                     "name": "update_frontmatter",
                     "arguments": json.dumps({
                         "path": "wiki/concepts/two.md",
                         "fields": {"tags": ["machine-learning"]},
                     }),
                 }},
            ]}),
            (CompletionResult(content="Merged ml into machine-learning."),
             {"role": "assistant", "content": "Merged ml into machine-learning."}),
        ]
        agent = KnowledgeAgent(vault=vault, provider=provider)
        responses = list(agent.chat("Merge ml into machine-learning"))
        assert any("↳ update_frontmatter" in r for r in responses)
        assert len(agent.plan_store.list_pending()) == 0
        assert "machine-learning" in vault.read_file("wiki/concepts/one.md")
        assert "machine-learning" in vault.read_file("wiki/concepts/two.md")
        assert "tags: [ml]" not in vault.read_file("wiki/concepts/one.md")

    def test_format_plan_for_restructure(self, vault: Vault) -> None:
        from noteweaver.plan import Plan, PlanStatus
        provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=provider)
        plan = Plan(
            id="plan-test",
            status=PlanStatus.PENDING,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            summary="Merge tag 'ml' into 'machine-learning'",
            targets=[],
            rationale="Standardize",
            intent="restructure",
            change_type="structural",
        )
        text = agent.format_plan(plan)
        assert "重构" in text
        assert "ml" in text
        assert "machine-learning" in text


# ======================================================================
# Similar tag detection: enhanced patterns
# ======================================================================


class TestSimilarTagReason:
    def test_substring(self) -> None:
        assert Vault._similar_tag_reason("react", "react-native") == "substring"

    def test_hyphen_variant(self) -> None:
        assert Vault._similar_tag_reason("machine-learning", "machinelearning") == "hyphen variant"

    def test_plural_s(self) -> None:
        assert Vault._similar_tag_reason("model", "models") == "plural"

    def test_plural_es(self) -> None:
        assert Vault._similar_tag_reason("process", "processes") == "plural"

    def test_plural_ies(self) -> None:
        assert Vault._similar_tag_reason("strategy", "strategies") == "plural"

    def test_edit_distance(self) -> None:
        reason = Vault._similar_tag_reason("flask", "flaks")
        assert reason is not None
        assert "edit distance" in reason

    def test_no_match(self) -> None:
        assert Vault._similar_tag_reason("python", "javascript") is None

    def test_identical_not_matched(self) -> None:
        assert Vault._similar_tag_reason("react", "react") is None

    def test_substring_still_detected(self) -> None:
        assert Vault._similar_tag_reason("react", "reactjs") == "substring"


class TestPluralPair:
    def test_simple_s(self) -> None:
        assert Vault._is_plural_pair("model", "models") is True

    def test_simple_es(self) -> None:
        assert Vault._is_plural_pair("process", "processes") is True

    def test_ies(self) -> None:
        assert Vault._is_plural_pair("strategy", "strategies") is True

    def test_not_plural(self) -> None:
        assert Vault._is_plural_pair("react", "reactive") is False

    def test_same(self) -> None:
        assert Vault._is_plural_pair("model", "model") is False

    def test_order_independent(self) -> None:
        assert Vault._is_plural_pair("models", "model") is True


class TestAuditSimilarTagsEnhanced:
    def test_hyphen_variant_detected_in_audit(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("A", tags=["machine-learning"]))
        vault.write_file("wiki/concepts/b.md", _page("B", tags=["machinelearning"]))
        report = vault.audit_vault()
        assert any(
            "hyphen" in st.get("reason", "")
            for st in report.get("similar_tags", [])
        )

    def test_plural_detected_in_audit(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", _page("A", tags=["model"]))
        vault.write_file("wiki/concepts/b.md", _page("B", tags=["models"]))
        report = vault.audit_vault()
        assert any(
            "plural" in st.get("reason", "")
            for st in report.get("similar_tags", [])
        )
