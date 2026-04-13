"""Tests for the new skill system.

Skills are SKILL.md files in .schema/skills/, injected into the system
prompt as <available_skills>, and triggered via read_page progressive
disclosure (following the openclaw pattern).

Sub-agents are spawned via the spawn_subagent tool for heavy tasks.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from noteweaver.adapters.provider import CompletionResult, ToolCall
from noteweaver.agent import KnowledgeAgent, _format_available_skills
from noteweaver.vault import Vault


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


@pytest.fixture
def agent(vault: Vault) -> KnowledgeAgent:
    mock_provider = MagicMock()
    return KnowledgeAgent(vault=vault, provider=mock_provider)


def _make_completion(content: str | None, tool_calls: list[dict] | None = None):
    """Create a (CompletionResult, raw_message) pair for mocking."""
    tcs = []
    raw_tcs = []
    if tool_calls:
        for tc in tool_calls:
            tcs.append(ToolCall(
                id=tc["id"],
                name=tc["name"],
                arguments=json.dumps(tc.get("arguments", {})),
            ))
            raw_tcs.append({
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc.get("arguments", {})),
                },
            })
    raw = {"role": "assistant", "content": content}
    if raw_tcs:
        raw["tool_calls"] = raw_tcs
    return (CompletionResult(content=content, tool_calls=tcs), raw)


# ======================================================================
# SKILL.md loading
# ======================================================================


class TestSkillLoading:
    def test_vault_init_seeds_default_skills(self, vault: Vault) -> None:
        skills_dir = vault.schema_dir / "skills"
        assert skills_dir.is_dir()
        assert (skills_dir / "import_sources" / "SKILL.md").is_file()
        assert (skills_dir / "organize_wiki" / "SKILL.md").is_file()

    def test_load_skills_returns_metadata(self, vault: Vault) -> None:
        skills = vault.load_skills()
        assert len(skills) >= 2
        names = {s["name"] for s in skills}
        assert "import_sources" in names
        assert "organize_wiki" in names

    def test_load_skills_has_description(self, vault: Vault) -> None:
        skills = vault.load_skills()
        for s in skills:
            assert s["description"]
            assert len(s["description"]) > 10

    def test_load_skills_has_location(self, vault: Vault) -> None:
        skills = vault.load_skills()
        for s in skills:
            assert s["location"].startswith(".schema/skills/")
            assert s["location"].endswith("/SKILL.md")

    def test_skill_md_readable_via_read_page(self, vault: Vault) -> None:
        content = vault.read_file(".schema/skills/import_sources/SKILL.md")
        assert "Import Sources" in content
        assert "spawn_subagent" in content

    def test_custom_skill_loaded(self, vault: Vault) -> None:
        custom_dir = vault.schema_dir / "skills" / "my_custom_skill"
        custom_dir.mkdir(parents=True, exist_ok=True)
        (custom_dir / "SKILL.md").write_text(
            "---\nname: my_custom_skill\n"
            "description: A custom skill for testing\n---\n\n"
            "# My Custom Skill\n\nDo something custom.\n",
            encoding="utf-8",
        )
        skills = vault.load_skills()
        names = {s["name"] for s in skills}
        assert "my_custom_skill" in names

    def test_skill_without_description_skipped(self, vault: Vault) -> None:
        bad_dir = vault.schema_dir / "skills" / "bad_skill"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "SKILL.md").write_text(
            "---\nname: bad_skill\n---\n\nNo description.\n",
            encoding="utf-8",
        )
        skills = vault.load_skills()
        names = {s["name"] for s in skills}
        assert "bad_skill" not in names

    def test_reinit_does_not_overwrite_skills(self, vault: Vault) -> None:
        skill_path = vault.schema_dir / "skills" / "import_sources" / "SKILL.md"
        skill_path.write_text("---\nname: import_sources\ndescription: Modified\n---\nCustom", encoding="utf-8")
        vault.init()
        content = skill_path.read_text(encoding="utf-8")
        assert "Modified" in content


# ======================================================================
# System prompt injection
# ======================================================================


class TestSkillInjection:
    def test_system_prompt_has_available_skills(self, agent: KnowledgeAgent) -> None:
        system_msg = agent.messages[0]["content"]
        assert "<available_skills>" in system_msg
        assert "import_sources" in system_msg
        assert "organize_wiki" in system_msg

    def test_system_prompt_has_skill_instructions(self, agent: KnowledgeAgent) -> None:
        system_msg = agent.messages[0]["content"]
        assert "read_page" in system_msg
        assert "SKILL.md" in system_msg
        assert "<location>" in system_msg

    def test_format_available_skills_empty(self) -> None:
        assert _format_available_skills([]) == ""

    def test_format_available_skills_xml(self) -> None:
        skills = [
            {"name": "test_skill", "description": "A test", "location": ".schema/skills/test/SKILL.md"},
        ]
        result = _format_available_skills(skills)
        assert "<available_skills>" in result
        assert "<name>test_skill</name>" in result
        assert "<description>A test</description>" in result
        assert "</available_skills>" in result

    def test_no_marker_instructions_in_prompt(self, agent: KnowledgeAgent) -> None:
        system_msg = agent.messages[0]["content"]
        assert "<<skill:" not in system_msg


# ======================================================================
# spawn_subagent tool
# ======================================================================


class TestSpawnSubagent:
    def test_spawn_subagent_in_schemas(self) -> None:
        from noteweaver.tools.definitions import TOOL_SCHEMAS
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert "spawn_subagent" in names

    def test_spawn_subagent_creates_independent_agent(
        self, vault: Vault,
    ) -> None:
        mock_provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=mock_provider)

        mock_provider.chat_completion.return_value = _make_completion(
            "Task completed: created 3 wiki pages."
        )

        result = agent._handle_spawn_subagent(
            "Process these source files into wiki pages."
        )

        assert "Sub-agent completed" in result
        assert "Task completed" in result
        assert mock_provider.chat_completion.called

    def test_spawn_subagent_empty_task_error(self, agent: KnowledgeAgent) -> None:
        result = agent._handle_spawn_subagent("")
        assert "Error" in result

    def test_spawn_subagent_does_not_share_transcript(
        self, vault: Vault,
    ) -> None:
        mock_provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=mock_provider)

        agent.messages.append({"role": "user", "content": "parent context"})
        agent.messages.append({"role": "assistant", "content": "parent reply"})

        parent_msg_count = len(agent.messages)

        mock_provider.chat_completion.return_value = _make_completion(
            "Sub-agent done."
        )

        agent._handle_spawn_subagent("Do something")

        assert len(agent.messages) == parent_msg_count

    def test_spawn_subagent_via_chat_tool_call(self, vault: Vault) -> None:
        mock_provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=mock_provider)

        mock_provider.chat_completion.side_effect = [
            _make_completion(None, [{
                "id": "tc1",
                "name": "spawn_subagent",
                "arguments": {"task": "Process cluster A: read sources/a.md, create wiki page."},
            }]),
            _make_completion("I've spawned a sub-agent to handle cluster A."),
            _make_completion("Sub-agent result: created wiki/concepts/a.md"),
        ]

        chunks = list(agent.chat("帮我导入 sources"))
        assert any("spawn_subagent" in c for c in chunks)

    def test_spawn_subagent_shares_vault(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/existing.md",
            "---\ntitle: Existing\ntype: note\n"
            "summary: Pre-existing page\ntags: [test]\n"
            "created: 2025-01-01\nupdated: 2025-01-01\n---\n\n# Existing\n",
        )

        mock_provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=mock_provider)

        mock_provider.chat_completion.return_value = _make_completion(
            None, [{
                "id": "tc1",
                "name": "read_page",
                "arguments": {"path": "wiki/concepts/existing.md"},
            }],
        )
        mock_provider.chat_completion.side_effect = [
            _make_completion(None, [{
                "id": "tc1",
                "name": "read_page",
                "arguments": {"path": "wiki/concepts/existing.md"},
            }]),
            _make_completion("Found the existing page about the topic."),
        ]

        result = agent._handle_spawn_subagent("Check if wiki has an existing page")
        assert "Found the existing page" in result


# ======================================================================
# Legacy skill system (backward compatibility)
# ======================================================================


class TestLegacySkillCompat:
    def test_run_skill_still_works(self, vault: Vault) -> None:
        """Legacy run_skill should still function for CLI commands."""
        from noteweaver.skills import get_skill
        skill = get_skill("import_sources")
        assert skill is not None

    def test_skill_registry_still_available(self) -> None:
        from noteweaver.skills import SKILL_REGISTRY, list_skills
        assert "import_sources" in SKILL_REGISTRY
        assert "organize_wiki" in SKILL_REGISTRY
        skills = list_skills()
        assert len(skills) >= 2


# ======================================================================
# Progressive disclosure flow
# ======================================================================


class TestProgressiveDisclosure:
    def test_model_can_read_skill_md(self, vault: Vault) -> None:
        """The model can read a SKILL.md file via read_page."""
        from noteweaver.tools.definitions import dispatch_tool
        result = dispatch_tool(
            vault, "read_page",
            {"path": ".schema/skills/import_sources/SKILL.md"},
        )
        assert "Import Sources" in result
        assert "Phase 1" in result
        assert "spawn_subagent" in result

    def test_model_can_read_organize_skill(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        result = dispatch_tool(
            vault, "read_page",
            {"path": ".schema/skills/organize_wiki/SKILL.md"},
        )
        assert "Organize Wiki" in result
        assert "Audit" in result

    def test_full_skill_flow_with_read_and_spawn(self, vault: Vault) -> None:
        """Simulate: model reads skill, then spawns subagent."""
        sources_dir = vault.root / "sources" / "notes"
        sources_dir.mkdir(parents=True, exist_ok=True)
        (sources_dir / "topic-a.md").write_text("# Topic A\n\nContent about A.")

        mock_provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=mock_provider)

        mock_provider.chat_completion.side_effect = [
            # Step 1: model reads the skill
            _make_completion(None, [{
                "id": "tc1",
                "name": "read_page",
                "arguments": {"path": ".schema/skills/import_sources/SKILL.md"},
            }]),
            # Step 2: model lists source files
            _make_completion(None, [{
                "id": "tc2",
                "name": "list_pages",
                "arguments": {"directory": "sources", "include_raw": True},
            }]),
            # Step 3: model spawns a subagent for the cluster
            _make_completion(None, [{
                "id": "tc3",
                "name": "spawn_subagent",
                "arguments": {
                    "task": "Import topic-a.md from sources/notes/ into wiki.",
                },
            }]),
            # (subagent's LLM call)
            _make_completion("Created wiki/concepts/topic-a.md"),
            # Step 4: model reports back
            _make_completion("Done! I imported 1 source file into the wiki."),
        ]

        chunks = list(agent.chat("帮我把 sources 里的文件导入知识库"))

        text_chunks = [c for c in chunks if not c.startswith("  ↳ ")]
        tool_chunks = [c for c in chunks if c.startswith("  ↳ ")]

        assert any("read_page" in c for c in tool_chunks)
        assert any("spawn_subagent" in c for c in tool_chunks)
        assert any("Done" in c or "imported" in c for c in text_chunks)
