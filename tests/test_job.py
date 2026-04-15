"""Tests for the job system — contract parsing, progress tracking, worker prompt assembly."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from noteweaver.vault import Vault
from noteweaver.agent import KnowledgeAgent
from noteweaver.job import (
    generate_job_id,
    parse_contract,
    parse_progress,
    extract_audit_criteria,
    check_audit_criteria,
    list_jobs,
    get_active_jobs,
    get_next_ready_job,
    get_running_job,
    update_contract_status,
    detect_stall,
    build_worker_prompt,
    format_active_jobs_summary,
)
from noteweaver.constants import JOB_DEFAULT_MAX_ITERATIONS, JOB_DIR


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


@pytest.fixture
def agent(vault: Vault) -> KnowledgeAgent:
    mock_provider = MagicMock()
    return KnowledgeAgent(vault=vault, provider=mock_provider)


SAMPLE_CONTRACT = """\
# Job: 批量导入论文

## Status
ready

## Goal
为 sources/ 下 20 篇论文整理进知识库。

## Acceptance Criteria
- [ ] 每篇论文有对应 wiki 页面 [audit: new_page_count matches source count]
- [ ] frontmatter 完整 [audit: missing_frontmatter = 0]
- [ ] 每页至少 2 个 wiki-link [audit: avg_links_per_page >= 2]
- [ ] 无孤儿页 [audit: orphan_pages = 0]
- [ ] 页面内容准确 [worker]

## Max Iterations
30

## Created
2026-04-15
"""

SAMPLE_PROGRESS = """\
# Progress: 批量导入论文

## Iteration 1 (2026-04-15 03:00)

### 本轮工作
- 处理了 5 篇论文

### 自评
前 5 篇处理顺利。

## Iteration 2 (2026-04-15 03:15)

### 本轮工作
- 处理了下一批 5 篇论文

### 自评
所有标准已满足，建议标记完成。
"""


# ======================================================================
# Job ID generation
# ======================================================================

class TestJobIdGeneration:
    def test_generates_valid_id(self) -> None:
        job_id = generate_job_id("批量导入论文")
        assert len(job_id) > 10
        assert "-" in job_id

    def test_id_contains_date(self) -> None:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        job_id = generate_job_id("test task")
        assert today in job_id

    def test_unique_ids(self) -> None:
        ids = {generate_job_id("test") for _ in range(10)}
        assert len(ids) == 10


# ======================================================================
# Contract parsing
# ======================================================================

class TestContractParsing:
    def test_parse_status(self) -> None:
        result = parse_contract(SAMPLE_CONTRACT)
        assert result["status"] == "ready"

    def test_parse_goal(self) -> None:
        result = parse_contract(SAMPLE_CONTRACT)
        assert "20 篇论文" in result["goal"]

    def test_parse_criteria(self) -> None:
        result = parse_contract(SAMPLE_CONTRACT)
        assert len(result["criteria"]) == 5

    def test_parse_max_iterations(self) -> None:
        result = parse_contract(SAMPLE_CONTRACT)
        assert result["max_iterations"] == 30

    def test_parse_created(self) -> None:
        result = parse_contract(SAMPLE_CONTRACT)
        assert result["created"] == "2026-04-15"

    def test_default_max_iterations(self) -> None:
        minimal = "## Status\ndraft\n\n## Goal\nDo something\n"
        result = parse_contract(minimal)
        assert result["max_iterations"] == JOB_DEFAULT_MAX_ITERATIONS

    def test_empty_contract(self) -> None:
        result = parse_contract("")
        assert result["status"] == "draft"
        assert result["goal"] == ""


# ======================================================================
# Audit criteria extraction
# ======================================================================

class TestAuditCriteria:
    def test_extract_audit_tags(self) -> None:
        criteria = [
            "[ ] frontmatter 完整 [audit: missing_frontmatter = 0]",
            "[ ] 页面内容准确 [worker]",
            "[ ] 无孤儿页 [audit: orphan_pages = 0]",
        ]
        audit = extract_audit_criteria(criteria)
        assert len(audit) == 2
        assert audit[0]["metric"] == "missing_frontmatter = 0"
        assert audit[1]["metric"] == "orphan_pages = 0"

    def test_no_audit_criteria(self) -> None:
        criteria = ["[ ] 页面内容准确 [worker]"]
        audit = extract_audit_criteria(criteria)
        assert len(audit) == 0

    def test_check_equals_zero_pass(self) -> None:
        audit_criteria = [{"text": "test", "metric": "orphan_pages = 0", "original": ""}]
        report = {"orphan_pages": []}
        results = check_audit_criteria(audit_criteria, report)
        assert results[0]["passed"] is True

    def test_check_equals_zero_fail(self) -> None:
        audit_criteria = [{"text": "test", "metric": "orphan_pages = 0", "original": ""}]
        report = {"orphan_pages": ["page1.md", "page2.md"]}
        results = check_audit_criteria(audit_criteria, report)
        assert results[0]["passed"] is False

    def test_check_gte_pass(self) -> None:
        audit_criteria = [{"text": "test", "metric": "avg_links_per_page >= 2", "original": ""}]
        report = {"avg_links_per_page": 3.5}
        results = check_audit_criteria(audit_criteria, report)
        assert results[0]["passed"] is True

    def test_check_gte_fail(self) -> None:
        audit_criteria = [{"text": "test", "metric": "avg_links_per_page >= 2", "original": ""}]
        report = {"avg_links_per_page": 1.2}
        results = check_audit_criteria(audit_criteria, report)
        assert results[0]["passed"] is False

    def test_missing_metric(self) -> None:
        audit_criteria = [{"text": "test", "metric": "nonexistent = 0", "original": ""}]
        report = {}
        results = check_audit_criteria(audit_criteria, report)
        assert results[0]["passed"] is False


# ======================================================================
# Progress parsing
# ======================================================================

class TestProgressParsing:
    def test_parse_iteration_count(self) -> None:
        result = parse_progress(SAMPLE_PROGRESS)
        assert result["iteration_count"] == 2

    def test_declares_complete(self) -> None:
        result = parse_progress(SAMPLE_PROGRESS)
        assert result["declares_complete"] is True

    def test_not_complete(self) -> None:
        progress = "## Iteration 1\n\n### 自评\n还有工作要做。\n"
        result = parse_progress(progress)
        assert result["declares_complete"] is False

    def test_empty_progress(self) -> None:
        result = parse_progress("")
        assert result["iteration_count"] == 0
        assert result["declares_complete"] is False


# ======================================================================
# Job directory scanning
# ======================================================================

class TestJobScanning:
    def _create_job(self, vault: Vault, job_id: str, status: str = "ready") -> Path:
        job_dir = vault.root / JOB_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        contract = SAMPLE_CONTRACT.replace("ready", status)
        (job_dir / "contract.md").write_text(contract, encoding="utf-8")
        return job_dir

    def test_list_empty(self, vault: Vault) -> None:
        assert list_jobs(vault) == []

    def test_list_one_job(self, vault: Vault) -> None:
        self._create_job(vault, "test-job-1")
        jobs = list_jobs(vault)
        assert len(jobs) == 1
        assert jobs[0]["id"] == "test-job-1"
        assert jobs[0]["status"] == "ready"

    def test_get_active_jobs(self, vault: Vault) -> None:
        self._create_job(vault, "job-ready", "ready")
        self._create_job(vault, "job-running", "running")
        self._create_job(vault, "job-completed", "completed")
        self._create_job(vault, "job-draft", "draft")
        active = get_active_jobs(vault)
        ids = {j["id"] for j in active}
        assert ids == {"job-ready", "job-running"}

    def test_get_next_ready(self, vault: Vault) -> None:
        self._create_job(vault, "aaa-first", "ready")
        self._create_job(vault, "zzz-second", "ready")
        job = get_next_ready_job(vault)
        assert job["id"] == "aaa-first"

    def test_get_next_ready_none(self, vault: Vault) -> None:
        self._create_job(vault, "running-job", "running")
        assert get_next_ready_job(vault) is None

    def test_get_running_job(self, vault: Vault) -> None:
        self._create_job(vault, "my-running", "running")
        job = get_running_job(vault)
        assert job is not None
        assert job["id"] == "my-running"

    def test_get_running_job_none(self, vault: Vault) -> None:
        self._create_job(vault, "ready-job", "ready")
        assert get_running_job(vault) is None


# ======================================================================
# Status updates
# ======================================================================

class TestStatusUpdates:
    def test_update_status(self, vault: Vault) -> None:
        job_dir = vault.root / JOB_DIR / "test-job"
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "contract.md").write_text(SAMPLE_CONTRACT, encoding="utf-8")

        update_contract_status(job_dir, "running")
        content = (job_dir / "contract.md").read_text(encoding="utf-8")
        result = parse_contract(content)
        assert result["status"] == "running"

    def test_update_to_completed(self, vault: Vault) -> None:
        job_dir = vault.root / JOB_DIR / "test-job"
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "contract.md").write_text(SAMPLE_CONTRACT, encoding="utf-8")

        update_contract_status(job_dir, "completed")
        content = (job_dir / "contract.md").read_text(encoding="utf-8")
        result = parse_contract(content)
        assert result["status"] == "completed"
        assert "Goal" in content


# ======================================================================
# Stall detection
# ======================================================================

class TestStallDetection:
    def test_no_stall_with_changes(self) -> None:
        diffs = [["file1.md"], ["file2.md"], ["file3.md"]]
        assert detect_stall({}, diffs) is False

    def test_stall_detected(self) -> None:
        diffs = [[], [], []]
        assert detect_stall({}, diffs) is True

    def test_not_enough_history(self) -> None:
        diffs = [[], []]
        assert detect_stall({}, diffs) is False

    def test_mixed_diffs_no_stall(self) -> None:
        diffs = [[], [], ["file.md"]]
        assert detect_stall({}, diffs) is False


# ======================================================================
# Worker prompt assembly
# ======================================================================

class TestWorkerPrompt:
    def test_includes_contract(self) -> None:
        prompt = build_worker_prompt(
            contract_content=SAMPLE_CONTRACT,
            progress_content="",
            diff_files=[],
            audit_summary="0 issues",
            iteration=1,
        )
        assert "批量导入论文" in prompt
        assert "Iteration 1" in prompt

    def test_includes_progress(self) -> None:
        prompt = build_worker_prompt(
            contract_content=SAMPLE_CONTRACT,
            progress_content=SAMPLE_PROGRESS,
            diff_files=[],
            audit_summary="",
            iteration=3,
        )
        assert "Recent Progress" in prompt

    def test_includes_diff_files(self) -> None:
        prompt = build_worker_prompt(
            contract_content=SAMPLE_CONTRACT,
            progress_content="",
            diff_files=["wiki/concepts/attention.md", "wiki/index.md"],
            audit_summary="",
            iteration=2,
        )
        assert "attention.md" in prompt

    def test_includes_audit(self) -> None:
        prompt = build_worker_prompt(
            contract_content=SAMPLE_CONTRACT,
            progress_content="",
            diff_files=[],
            audit_summary="5 issues found: 3 orphan pages, 2 missing frontmatter",
            iteration=1,
        )
        assert "5 issues found" in prompt


# ======================================================================
# Active jobs summary for main agent
# ======================================================================

class TestActiveJobsSummary:
    def _create_job(self, vault: Vault, job_id: str, status: str = "running") -> None:
        job_dir = vault.root / JOB_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        contract = SAMPLE_CONTRACT.replace("ready", status)
        (job_dir / "contract.md").write_text(contract, encoding="utf-8")

    def test_no_active_jobs(self, vault: Vault) -> None:
        result = format_active_jobs_summary(vault)
        assert result == ""

    def test_with_active_job(self, vault: Vault) -> None:
        self._create_job(vault, "test-job", "running")
        result = format_active_jobs_summary(vault)
        assert "Active Jobs" in result
        assert "test-job" in result

    def test_completed_jobs_excluded(self, vault: Vault) -> None:
        self._create_job(vault, "done-job", "completed")
        result = format_active_jobs_summary(vault)
        assert result == ""


# ======================================================================
# Audit tool handler
# ======================================================================

class TestAuditTool:
    def test_audit_vault_tool(self, vault: Vault) -> None:
        from noteweaver.tools.handlers_read import handle_audit_vault
        result = handle_audit_vault(vault)
        assert "Vault Audit" in result

    def test_audit_vault_dispatch(self, vault: Vault) -> None:
        from noteweaver.tools.dispatch import dispatch_tool
        result = dispatch_tool(vault, "audit_vault", {})
        assert "Vault Audit" in result


# ======================================================================
# Tool schema / policy
# ======================================================================

class TestAuditToolSchema:
    def test_audit_vault_in_schemas(self) -> None:
        from noteweaver.tools.schemas import TOOL_SCHEMAS
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert "audit_vault" in names

    def test_schema_count_updated(self) -> None:
        from noteweaver.tools.schemas import TOOL_SCHEMAS
        assert len(TOOL_SCHEMAS) == 11

    def test_audit_vault_in_observation_schemas(self) -> None:
        from noteweaver.tools.schemas import OBSERVATION_SCHEMAS
        names = {s["function"]["name"] for s in OBSERVATION_SCHEMAS}
        assert "audit_vault" in names

    def test_audit_vault_risk_tier(self) -> None:
        from noteweaver.tools.policy import TOOL_TIERS, RiskTier
        assert TOOL_TIERS["audit_vault"] == RiskTier.READ

    def test_audit_vault_policy_allows(self) -> None:
        from noteweaver.tools.policy import check_pre_dispatch, PolicyContext
        ctx = PolicyContext()
        verdict = check_pre_dispatch("audit_vault", {}, ctx)
        assert verdict.allowed is True


# ======================================================================
# Job worker system prompt
# ======================================================================

class TestJobWorkerSystemPrompt:
    def test_build_job_system_prompt(self, agent: KnowledgeAgent) -> None:
        prompt = agent._build_job_system_prompt()
        assert "Job Worker Protocols" in prompt
        assert "NoteWeaver" in prompt

    def test_job_prompt_includes_schema(self, vault: Vault) -> None:
        mock_provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=mock_provider)
        prompt = agent._build_job_system_prompt()
        assert "NoteWeaver" in prompt

    def test_job_prompt_excludes_normal_protocols(self, vault: Vault) -> None:
        (vault.schema_dir / "protocols.md").write_text(
            "# Normal Protocols\nSome rules", encoding="utf-8",
        )
        mock_provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=mock_provider)
        prompt = agent._build_job_system_prompt()
        assert "Normal Protocols" not in prompt
        assert "Job Worker Protocols" in prompt


# ======================================================================
# Active jobs injected into main agent context
# ======================================================================

class TestActiveJobsInjection:
    def test_no_jobs_no_injection(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "hello"})
        messages = agent._build_messages_for_query()
        system = messages[0]["content"]
        assert "Active Jobs" not in system

    def test_active_job_injected(self, agent: KnowledgeAgent) -> None:
        job_dir = agent.vault.root / JOB_DIR / "test-job"
        job_dir.mkdir(parents=True, exist_ok=True)
        contract = SAMPLE_CONTRACT.replace("ready", "running")
        (job_dir / "contract.md").write_text(contract, encoding="utf-8")

        agent.messages.append({"role": "user", "content": "hello"})
        messages = agent._build_messages_for_query()
        system = messages[0]["content"]
        assert "Active Jobs" in system
        assert "test-job" in system
