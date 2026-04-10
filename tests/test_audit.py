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
        report = vault.audit_vault()
        assert len(report["stale_imports"]) == 1
        assert report["stale_imports"][0]["path"] == "wiki/concepts/stale.md"

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
        assert "Existing tags:" in ctx
        assert "Existing hubs:" in ctx

    def test_with_pages(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/ml.md",
            _page("ML", ptype="hub", tags=["ml"]),
        )
        vault.write_file(
            "wiki/concepts/dl.md",
            _page("Deep Learning", tags=["ml", "dl"]),
        )
        ctx = vault.scan_vault_context()
        assert "ml" in ctx
        assert "dl" in ctx
        assert "ML" in ctx
        assert "Deep Learning" in ctx


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
                        name="append_section",
                        arguments=json.dumps({
                            "path": "wiki/concepts/react-hooks.md",
                            "heading": "useState",
                            "content": "useState manages local component state.",
                        }),
                    ),
                ],
            ),
            {"role": "assistant", "tool_calls": []},
        )

        plan = agent.generate_organize_plan()
        assert plan is not None
        assert len(plan) == 1
        assert plan[0]["name"] == "append_section"
        assert "react-hooks" in plan[0]["arguments"]["path"]

    def test_plan_persisted_to_disk(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "Important content"})
        agent.messages.append({"role": "assistant", "content": "Noted."})
        agent.messages.append({"role": "user", "content": "More important stuff."})
        agent.provider.chat_completion.return_value = (
            CompletionResult(
                content=None,
                tool_calls=[
                    ToolCall(id="tc1", name="append_log",
                             arguments=json.dumps({"entry_type": "test", "title": "X"})),
                ],
            ),
            {"role": "assistant", "tool_calls": []},
        )
        plan = agent.generate_organize_plan()
        assert plan is not None
        loaded = agent._load_pending_plan()
        assert loaded == plan

    def test_provider_error_returns_none(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "test"})
        agent.messages.append({"role": "assistant", "content": "reply"})
        agent.messages.append({"role": "user", "content": "more"})
        agent.provider.chat_completion.side_effect = Exception("API down")
        plan = agent.generate_organize_plan()
        assert plan is None


class TestFormatOrganizePlan:
    def test_format_write_page(self, agent: KnowledgeAgent) -> None:
        plan = [{"name": "write_page", "arguments": {"path": "wiki/concepts/test.md", "content": "..."}}]
        text = agent.format_organize_plan(plan)
        assert "新建页面" in text
        assert "wiki/concepts/test.md" in text

    def test_format_append_section(self, agent: KnowledgeAgent) -> None:
        plan = [{"name": "append_section", "arguments": {"path": "wiki/concepts/x.md", "heading": "New"}}]
        text = agent.format_organize_plan(plan)
        assert "添加 section" in text

    def test_format_update_frontmatter(self, agent: KnowledgeAgent) -> None:
        plan = [{"name": "update_frontmatter", "arguments": {"path": "wiki/concepts/x.md", "fields": {"tags": ["a"]}}}]
        text = agent.format_organize_plan(plan)
        assert "更新" in text
        assert "tags" in text

    def test_format_add_related_link(self, agent: KnowledgeAgent) -> None:
        plan = [{"name": "add_related_link", "arguments": {"path": "wiki/concepts/x.md", "title": "Y"}}]
        text = agent.format_organize_plan(plan)
        assert "添加链接" in text

    def test_format_empty_plan(self, agent: KnowledgeAgent) -> None:
        assert agent.format_organize_plan([]) == ""


class TestExecuteOrganizePlan:
    def test_execute_appends_section(self, vault: Vault, agent: KnowledgeAgent) -> None:
        vault.write_file(
            "wiki/concepts/test.md",
            _page("Test", summary="A test page"),
        )
        plan = [{
            "name": "append_section",
            "arguments": {
                "path": "wiki/concepts/test.md",
                "heading": "New Info",
                "content": "Some new content.",
            },
        }]
        result = agent.execute_organize_plan(plan)
        assert "1 成功" in result
        content = vault.read_file("wiki/concepts/test.md")
        assert "## New Info" in content

    def test_execute_updates_frontmatter(self, vault: Vault, agent: KnowledgeAgent) -> None:
        vault.write_file("wiki/concepts/test.md", _page("Test"))
        plan = [{
            "name": "update_frontmatter",
            "arguments": {
                "path": "wiki/concepts/test.md",
                "fields": {"tags": ["updated-tag"]},
            },
        }]
        result = agent.execute_organize_plan(plan)
        assert "1 成功" in result
        from noteweaver.frontmatter import extract_frontmatter
        content = vault.read_file("wiki/concepts/test.md")
        fm = extract_frontmatter(content)
        assert "updated-tag" in fm["tags"]

    def test_execute_handles_errors(self, vault: Vault, agent: KnowledgeAgent) -> None:
        plan = [{
            "name": "read_page",
            "arguments": {"path": "wiki/concepts/nonexistent.md"},
        }]
        result = agent.execute_organize_plan(plan)
        assert "1 项操作" in result

    def test_execute_loads_from_disk(self, vault: Vault, agent: KnowledgeAgent) -> None:
        vault.write_file("wiki/concepts/test.md", _page("Test", summary="A test page"))
        plan = [{
            "name": "add_related_link",
            "arguments": {"path": "wiki/concepts/test.md", "title": "Other"},
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
        plan = [{"name": "vault_stats", "arguments": {}}]
        old_boundary = agent._last_organize_boundary
        agent.execute_organize_plan(plan)
        assert agent._last_organize_boundary > old_boundary

    def test_multiple_actions(self, vault: Vault, agent: KnowledgeAgent) -> None:
        vault.write_file("wiki/concepts/a.md", _page("A", summary="Page A"))
        vault.write_file("wiki/concepts/b.md", _page("B", summary="Page B"))
        plan = [
            {"name": "add_related_link", "arguments": {"path": "wiki/concepts/a.md", "title": "B"}},
            {"name": "add_related_link", "arguments": {"path": "wiki/concepts/b.md", "title": "A"}},
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
        assert agent._load_pending_plan() is None

    def test_write_tools_intercepted(self, vault: Vault) -> None:
        from noteweaver.adapters.provider import CompletionResult, ToolCall
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            (CompletionResult(content=None, tool_calls=[
                ToolCall(id="tc1", name="write_page",
                         arguments=json.dumps({
                             "path": "wiki/concepts/test.md",
                             "content": "---\ntitle: Test\ntype: note\n---\n# Test",
                         })),
            ]), {"role": "assistant", "tool_calls": [
                {"id": "tc1", "type": "function",
                 "function": {"name": "write_page", "arguments": json.dumps({
                     "path": "wiki/concepts/test.md",
                     "content": "---\ntitle: Test\ntype: note\n---\n# Test",
                 })}}
            ]}),
            (CompletionResult(content="I've proposed creating the page."),
             {"role": "assistant", "content": "I've proposed creating the page."}),
        ]
        agent = KnowledgeAgent(vault=vault, provider=provider)
        responses = list(agent.chat("Create a test page"))
        assert any("📋" in r for r in responses)
        assert not (vault.root / "wiki" / "concepts" / "test.md").exists()
        plan = agent._load_pending_plan()
        assert plan is not None
        assert len(plan) == 1
        assert plan[0]["name"] == "write_page"

    def test_mixed_read_write_in_one_turn(self, vault: Vault) -> None:
        from noteweaver.adapters.provider import CompletionResult, ToolCall
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            (CompletionResult(content=None, tool_calls=[
                ToolCall(id="tc1", name="read_page",
                         arguments=json.dumps({"path": "wiki/index.md"})),
                ToolCall(id="tc2", name="append_log",
                         arguments=json.dumps({"entry_type": "test", "title": "X"})),
            ]), {"role": "assistant", "tool_calls": [
                {"id": "tc1", "type": "function",
                 "function": {"name": "read_page", "arguments": json.dumps({"path": "wiki/index.md"})}},
                {"id": "tc2", "type": "function",
                 "function": {"name": "append_log", "arguments": json.dumps({"entry_type": "test", "title": "X"})}},
            ]}),
            (CompletionResult(content="Done."), {"role": "assistant", "content": "Done."}),
        ]
        agent = KnowledgeAgent(vault=vault, provider=provider)
        responses = list(agent.chat("Do something"))
        assert any("↳" in r for r in responses)
        assert any("📋" in r for r in responses)
        plan = agent._load_pending_plan()
        assert plan is not None
        assert len(plan) == 1
        assert plan[0]["name"] == "append_log"

    def test_no_writes_no_plan(self, vault: Vault) -> None:
        from noteweaver.adapters.provider import CompletionResult
        provider = MagicMock()
        provider.chat_completion.return_value = (
            CompletionResult(content="Just a chat."),
            {"role": "assistant", "content": "Just a chat."},
        )
        agent = KnowledgeAgent(vault=vault, provider=provider)
        list(agent.chat("Hello"))
        assert agent._load_pending_plan() is None


class TestIsWriteTool:
    def test_read_tools(self) -> None:
        agent = KnowledgeAgent.__new__(KnowledgeAgent)
        for tool in ["read_page", "list_page_summaries", "search_vault",
                      "vault_stats", "get_backlinks", "find_existing_page",
                      "read_transcript", "fetch_url"]:
            assert not agent._is_write_tool(tool), f"{tool} should be read"

    def test_write_tools(self) -> None:
        agent = KnowledgeAgent.__new__(KnowledgeAgent)
        for tool in ["write_page", "append_section", "append_to_section",
                      "update_frontmatter", "add_related_link", "append_log",
                      "save_source", "archive_page", "import_files",
                      "promote_insight", "apply_organize_plan"]:
            assert agent._is_write_tool(tool), f"{tool} should be write"


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
        assert any("hub" in r.lower() or "链接" in r for r in report)

    def test_already_linked_page_no_action(self, vault: Vault, agent: KnowledgeAgent) -> None:
        """A page that already has inbound links needs no disclosure fix."""
        vault.write_file("wiki/concepts/a.md", _page("A", related="- [[B]]"))
        vault.write_file("wiki/concepts/b.md", _page("B", related="- [[A]]"))
        plan = [{"name": "append_section", "arguments": {
            "path": "wiki/concepts/a.md",
            "heading": "New",
            "content": "x",
        }}]
        report = agent._ensure_progressive_disclosure(plan)
        assert len(report) == 0


# ======================================================================
# Stale import hint in list_page_summaries
# ======================================================================


class TestStaleImportHint:
    def test_no_imported_no_hint(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        vault.write_file("wiki/concepts/clean.md", _page("Clean", tags=["ml"]))
        result = dispatch_tool(vault, "list_page_summaries", {"directory": "wiki/concepts"})
        assert "still tagged [imported]" not in result

    def test_imported_shows_hint(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        vault.write_file("wiki/concepts/imp.md", _page("Imp", tags=["imported"]))
        result = dispatch_tool(vault, "list_page_summaries", {"directory": "wiki/concepts"})
        assert "1 file(s) still tagged [imported]" in result
