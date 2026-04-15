"""Tests for the Job data model and JobStore."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from noteweaver.job import (
    Job,
    JobStatus,
    JobStore,
    StepRecord,
    WriteScope,
    generate_job_id,
    _is_valid_transition,
    _TERMINAL_STATUSES,
)


@pytest.fixture
def meta_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".meta"
    d.mkdir()
    return d


@pytest.fixture
def store(meta_dir: Path) -> JobStore:
    return JobStore(meta_dir)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _make_job(**overrides: object) -> Job:
    now = _now_iso()
    defaults: dict = dict(
        id=generate_job_id(),
        status=JobStatus.DRAFT,
        created_at=now,
        updated_at=now,
        goal="Import 20 papers into the wiki",
        acceptance_criteria=["Each paper has a concept page", "No orphan pages"],
        evaluator_prompt="Check page quality",
        write_scope=WriteScope(
            allowed_path_prefixes=["wiki/concepts/", "sources/"],
            allowed_tools=["write_page", "append_section"],
            max_pages=50,
        ),
        max_iterations=10,
    )
    defaults.update(overrides)
    return Job(**defaults)


def _make_step(iteration: int = 1, **overrides: object) -> StepRecord:
    now = _now_iso()
    defaults: dict = dict(
        iteration=iteration,
        started_at=now,
        completed_at=now,
        actions_taken=["write_page: wiki/concepts/paper1.md"],
        structural_check={"frontmatter_complete": True},
        evaluation=None,
        evaluation_passed=None,
    )
    defaults.update(overrides)
    return StepRecord(**defaults)


# ==================================================================
# Job ID generation
# ==================================================================

class TestJobId:
    def test_format(self) -> None:
        jid = generate_job_id()
        assert jid.startswith("job-")
        parts = jid.split("-")
        assert len(parts) == 3

    def test_uniqueness(self) -> None:
        ids = {generate_job_id() for _ in range(50)}
        assert len(ids) == 50


# ==================================================================
# WriteScope
# ==================================================================

class TestWriteScope:
    def test_defaults(self) -> None:
        ws = WriteScope()
        assert ws.allowed_path_prefixes == []
        assert ws.allowed_tools == []
        assert ws.max_pages == 50

    def test_roundtrip(self) -> None:
        ws = WriteScope(
            allowed_path_prefixes=["wiki/concepts/"],
            allowed_tools=["write_page"],
            max_pages=20,
        )
        d = ws.to_dict()
        restored = WriteScope.from_dict(d)
        assert restored.allowed_path_prefixes == ["wiki/concepts/"]
        assert restored.allowed_tools == ["write_page"]
        assert restored.max_pages == 20

    def test_from_dict_missing_keys(self) -> None:
        ws = WriteScope.from_dict({})
        assert ws.allowed_path_prefixes == []
        assert ws.max_pages == 50


# ==================================================================
# StepRecord
# ==================================================================

class TestStepRecord:
    def test_roundtrip(self) -> None:
        step = _make_step(
            iteration=3,
            evaluation="Quality is good",
            evaluation_passed=True,
        )
        d = step.to_dict()
        restored = StepRecord.from_dict(d)
        assert restored.iteration == 3
        assert restored.evaluation == "Quality is good"
        assert restored.evaluation_passed is True
        assert restored.actions_taken == step.actions_taken

    def test_minimal(self) -> None:
        step = StepRecord(iteration=1, started_at=_now_iso(), completed_at=_now_iso())
        d = step.to_dict()
        restored = StepRecord.from_dict(d)
        assert restored.actions_taken == []
        assert restored.structural_check == {}
        assert restored.evaluation is None
        assert restored.evaluation_passed is None


# ==================================================================
# Job serialization
# ==================================================================

class TestJobSerialization:
    def test_to_dict_status_is_string(self) -> None:
        job = _make_job()
        d = job.to_dict()
        assert d["status"] == "draft"
        assert isinstance(d["write_scope"], dict)
        assert isinstance(d["progress"], list)

    def test_from_dict(self) -> None:
        job = _make_job()
        d = job.to_dict()
        restored = Job.from_dict(d)
        assert restored.id == job.id
        assert restored.status == JobStatus.DRAFT
        assert restored.goal == job.goal

    def test_roundtrip_with_progress(self) -> None:
        job = _make_job()
        job.progress = [
            _make_step(1),
            _make_step(2, evaluation="Looks good", evaluation_passed=True),
        ]
        job.iteration_count = 2
        job.last_evaluation = "Looks good"
        d = job.to_dict()
        restored = Job.from_dict(d)
        assert len(restored.progress) == 2
        assert restored.progress[1].evaluation == "Looks good"
        assert restored.iteration_count == 2

    def test_from_dict_unknown_status_defaults_to_draft(self) -> None:
        d = _make_job().to_dict()
        d["status"] = "banana"
        restored = Job.from_dict(d)
        assert restored.status == JobStatus.DRAFT

    def test_from_dict_empty_write_scope(self) -> None:
        d = _make_job().to_dict()
        d["write_scope"] = {}
        restored = Job.from_dict(d)
        assert restored.write_scope.max_pages == 50

    def test_json_roundtrip(self) -> None:
        job = _make_job()
        text = json.dumps(job.to_dict(), ensure_ascii=False, indent=2)
        data = json.loads(text)
        restored = Job.from_dict(data)
        assert restored.id == job.id
        assert restored.write_scope.allowed_path_prefixes == job.write_scope.allowed_path_prefixes


# ==================================================================
# Job properties
# ==================================================================

class TestJobProperties:
    @pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED])
    def test_is_terminal(self, status: JobStatus) -> None:
        job = _make_job(status=status)
        assert job.is_terminal

    @pytest.mark.parametrize("status", [
        JobStatus.DRAFT, JobStatus.READY, JobStatus.RUNNING,
        JobStatus.EVALUATING, JobStatus.PAUSED, JobStatus.BLOCKED,
    ])
    def test_is_not_terminal(self, status: JobStatus) -> None:
        job = _make_job(status=status)
        assert not job.is_terminal

    @pytest.mark.parametrize("status", [JobStatus.READY, JobStatus.RUNNING, JobStatus.EVALUATING])
    def test_is_active(self, status: JobStatus) -> None:
        job = _make_job(status=status)
        assert job.is_active

    @pytest.mark.parametrize("status", [
        JobStatus.DRAFT, JobStatus.PAUSED, JobStatus.BLOCKED,
        JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED,
    ])
    def test_is_not_active(self, status: JobStatus) -> None:
        job = _make_job(status=status)
        assert not job.is_active


# ==================================================================
# State machine transitions
# ==================================================================

class TestStateTransitions:
    def test_draft_to_ready(self) -> None:
        assert _is_valid_transition(JobStatus.DRAFT, JobStatus.READY)

    def test_draft_to_cancelled(self) -> None:
        assert _is_valid_transition(JobStatus.DRAFT, JobStatus.CANCELLED)

    def test_draft_to_running_invalid(self) -> None:
        assert not _is_valid_transition(JobStatus.DRAFT, JobStatus.RUNNING)

    def test_ready_to_running(self) -> None:
        assert _is_valid_transition(JobStatus.READY, JobStatus.RUNNING)

    def test_running_to_evaluating(self) -> None:
        assert _is_valid_transition(JobStatus.RUNNING, JobStatus.EVALUATING)

    def test_evaluating_to_running(self) -> None:
        assert _is_valid_transition(JobStatus.EVALUATING, JobStatus.RUNNING)

    def test_evaluating_to_completed(self) -> None:
        assert _is_valid_transition(JobStatus.EVALUATING, JobStatus.COMPLETED)

    def test_running_to_paused(self) -> None:
        assert _is_valid_transition(JobStatus.RUNNING, JobStatus.PAUSED)

    def test_running_to_blocked(self) -> None:
        assert _is_valid_transition(JobStatus.RUNNING, JobStatus.BLOCKED)

    def test_running_to_failed(self) -> None:
        assert _is_valid_transition(JobStatus.RUNNING, JobStatus.FAILED)

    def test_paused_to_ready(self) -> None:
        assert _is_valid_transition(JobStatus.PAUSED, JobStatus.READY)

    def test_blocked_to_ready(self) -> None:
        assert _is_valid_transition(JobStatus.BLOCKED, JobStatus.READY)

    def test_terminal_states_have_no_transitions(self) -> None:
        for status in _TERMINAL_STATUSES:
            for target in JobStatus:
                assert not _is_valid_transition(status, target)

    def test_all_non_terminal_can_cancel(self) -> None:
        for status in JobStatus:
            if status not in _TERMINAL_STATUSES:
                assert _is_valid_transition(status, JobStatus.CANCELLED)


# ==================================================================
# JobStore CRUD
# ==================================================================

class TestJobStoreCRUD:
    def test_save_and_load(self, store: JobStore) -> None:
        job = _make_job()
        store.save(job)
        loaded = store.load(job.id)
        assert loaded is not None
        assert loaded.id == job.id
        assert loaded.status == JobStatus.DRAFT
        assert loaded.goal == job.goal

    def test_load_nonexistent(self, store: JobStore) -> None:
        assert store.load("job-nonexistent") is None

    def test_save_updates_updated_at(self, store: JobStore) -> None:
        job = _make_job()
        old_ts = job.updated_at
        time.sleep(0.01)
        store.save(job)
        loaded = store.load(job.id)
        assert loaded is not None
        assert loaded.updated_at >= old_ts

    def test_delete(self, store: JobStore) -> None:
        job = _make_job()
        store.save(job)
        assert store.delete(job.id)
        assert store.load(job.id) is None

    def test_delete_nonexistent(self, store: JobStore) -> None:
        assert not store.delete("job-nope")

    def test_corrupted_json_returns_none(self, store: JobStore, meta_dir: Path) -> None:
        jobs_dir = meta_dir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        bad_file = jobs_dir / "job-bad.json"
        bad_file.write_text("{invalid json", encoding="utf-8")
        assert store.load("job-bad") is None


# ==================================================================
# JobStore queries
# ==================================================================

class TestJobStoreQueries:
    def test_list_all(self, store: JobStore) -> None:
        for i in range(5):
            store.save(_make_job(id=f"job-{i:03d}"))
        jobs = store.list_all()
        assert len(jobs) == 5

    def test_list_all_limit(self, store: JobStore) -> None:
        for i in range(5):
            store.save(_make_job(id=f"job-{i:03d}"))
        jobs = store.list_all(limit=3)
        assert len(jobs) == 3

    def test_list_all_sorted_newest_first(self, store: JobStore) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
        new_ts = _now_iso()
        store.save(_make_job(id="job-old", created_at=old_ts))
        store.save(_make_job(id="job-new", created_at=new_ts))
        jobs = store.list_all()
        assert jobs[0].id == "job-new"
        assert jobs[1].id == "job-old"

    def test_list_by_status(self, store: JobStore) -> None:
        store.save(_make_job(id="job-d1", status=JobStatus.DRAFT))
        store.save(_make_job(id="job-r1", status=JobStatus.READY))
        store.save(_make_job(id="job-d2", status=JobStatus.DRAFT))
        store.save(_make_job(id="job-c1", status=JobStatus.COMPLETED))

        drafts = store.list_by_status(JobStatus.DRAFT)
        assert len(drafts) == 2
        assert all(j.status == JobStatus.DRAFT for j in drafts)

        ready = store.list_by_status(JobStatus.READY)
        assert len(ready) == 1

        active = store.list_by_status(JobStatus.DRAFT, JobStatus.READY)
        assert len(active) == 3

    def test_list_empty_store(self, store: JobStore) -> None:
        assert store.list_all() == []
        assert store.list_by_status(JobStatus.DRAFT) == []


# ==================================================================
# JobStore.next_ready()
# ==================================================================

class TestNextReady:
    def test_returns_oldest_ready(self, store: JobStore) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
        new_ts = _now_iso()
        store.save(_make_job(id="job-old", status=JobStatus.READY, created_at=old_ts))
        store.save(_make_job(id="job-new", status=JobStatus.READY, created_at=new_ts))
        nxt = store.next_ready()
        assert nxt is not None
        assert nxt.id == "job-old"

    def test_returns_none_when_running_exists(self, store: JobStore) -> None:
        store.save(_make_job(id="job-ready", status=JobStatus.READY))
        store.save(_make_job(id="job-running", status=JobStatus.RUNNING))
        assert store.next_ready() is None

    def test_returns_none_when_evaluating_exists(self, store: JobStore) -> None:
        store.save(_make_job(id="job-ready", status=JobStatus.READY))
        store.save(_make_job(id="job-eval", status=JobStatus.EVALUATING))
        assert store.next_ready() is None

    def test_returns_none_when_no_ready(self, store: JobStore) -> None:
        store.save(_make_job(id="job-draft", status=JobStatus.DRAFT))
        assert store.next_ready() is None

    def test_returns_none_on_empty_store(self, store: JobStore) -> None:
        assert store.next_ready() is None

    def test_ignores_terminal_jobs(self, store: JobStore) -> None:
        store.save(_make_job(id="job-done", status=JobStatus.COMPLETED))
        store.save(_make_job(id="job-ready", status=JobStatus.READY))
        nxt = store.next_ready()
        assert nxt is not None
        assert nxt.id == "job-ready"


# ==================================================================
# JobStore.update_status()
# ==================================================================

class TestUpdateStatus:
    def test_valid_transition(self, store: JobStore) -> None:
        job = _make_job(status=JobStatus.DRAFT)
        store.save(job)
        updated = store.update_status(job.id, JobStatus.READY)
        assert updated is not None
        assert updated.status == JobStatus.READY
        loaded = store.load(job.id)
        assert loaded.status == JobStatus.READY

    def test_invalid_transition_returns_none(self, store: JobStore) -> None:
        job = _make_job(status=JobStatus.DRAFT)
        store.save(job)
        result = store.update_status(job.id, JobStatus.RUNNING)
        assert result is None
        loaded = store.load(job.id)
        assert loaded.status == JobStatus.DRAFT

    def test_nonexistent_job_returns_none(self, store: JobStore) -> None:
        assert store.update_status("job-nope", JobStatus.READY) is None

    def test_with_extra_fields(self, store: JobStore) -> None:
        job = _make_job(status=JobStatus.RUNNING)
        store.save(job)
        updated = store.update_status(
            job.id, JobStatus.BLOCKED,
            blocked_reason="Need user input on classification scheme",
        )
        assert updated is not None
        assert updated.blocked_reason == "Need user input on classification scheme"
        loaded = store.load(job.id)
        assert loaded.blocked_reason == "Need user input on classification scheme"

    def test_full_lifecycle(self, store: JobStore) -> None:
        """Walk through the happy-path lifecycle: DRAFT → READY → RUNNING → EVALUATING → COMPLETED."""
        job = _make_job(status=JobStatus.DRAFT)
        store.save(job)

        store.update_status(job.id, JobStatus.READY)
        store.update_status(job.id, JobStatus.RUNNING)
        store.update_status(job.id, JobStatus.EVALUATING)
        result = store.update_status(job.id, JobStatus.COMPLETED)
        assert result is not None
        assert result.status == JobStatus.COMPLETED
        assert result.is_terminal

    def test_cancel_from_any_non_terminal(self, store: JobStore) -> None:
        for status in [JobStatus.DRAFT, JobStatus.READY, JobStatus.RUNNING,
                       JobStatus.EVALUATING, JobStatus.PAUSED, JobStatus.BLOCKED]:
            job = _make_job(id=f"job-cancel-{status.value}", status=status)
            store.save(job)
            updated = store.update_status(job.id, JobStatus.CANCELLED)
            assert updated is not None
            assert updated.status == JobStatus.CANCELLED


# ==================================================================
# JobStore.append_step() / load_step()
# ==================================================================

class TestStepPersistence:
    def test_append_and_load_step(self, store: JobStore) -> None:
        job = _make_job(status=JobStatus.RUNNING)
        store.save(job)

        step = _make_step(iteration=1)
        path = store.append_step(job.id, step)
        assert path is not None
        assert path.exists()
        assert "step-001.json" in path.name

        loaded_step = store.load_step(job.id, 1)
        assert loaded_step is not None
        assert loaded_step.iteration == 1
        assert loaded_step.actions_taken == step.actions_taken

    def test_append_step_updates_job(self, store: JobStore) -> None:
        job = _make_job(status=JobStatus.RUNNING)
        store.save(job)

        store.append_step(job.id, _make_step(iteration=1))
        store.append_step(job.id, _make_step(
            iteration=2,
            evaluation="Looks good",
            evaluation_passed=True,
        ))

        loaded = store.load(job.id)
        assert loaded.iteration_count == 2
        assert len(loaded.progress) == 2
        assert loaded.last_evaluation == "Looks good"

    def test_append_step_nonexistent_job(self, store: JobStore) -> None:
        step = _make_step(iteration=1)
        assert store.append_step("job-nope", step) is None

    def test_load_step_nonexistent(self, store: JobStore) -> None:
        assert store.load_step("job-nope", 1) is None

    def test_multiple_steps(self, store: JobStore) -> None:
        job = _make_job(status=JobStatus.RUNNING)
        store.save(job)

        for i in range(1, 6):
            store.append_step(job.id, _make_step(
                iteration=i,
                actions_taken=[f"action_{i}"],
            ))

        loaded = store.load(job.id)
        assert loaded.iteration_count == 5
        assert len(loaded.progress) == 5

        for i in range(1, 6):
            step = store.load_step(job.id, i)
            assert step is not None
            assert step.iteration == i
            assert step.actions_taken == [f"action_{i}"]

    def test_step_with_evaluation_sets_last_evaluation(self, store: JobStore) -> None:
        job = _make_job(status=JobStatus.RUNNING)
        store.save(job)

        store.append_step(job.id, _make_step(iteration=1, evaluation="First eval"))
        store.append_step(job.id, _make_step(iteration=2))
        store.append_step(job.id, _make_step(iteration=3, evaluation="Third eval"))

        loaded = store.load(job.id)
        assert loaded.last_evaluation == "Third eval"


# ==================================================================
# Edge cases: path sanitization
# ==================================================================

class TestPathSanitization:
    def test_slash_in_id(self, store: JobStore) -> None:
        job = _make_job(id="job-../../etc/passwd")
        store.save(job)
        loaded = store.load("job-../../etc/passwd")
        assert loaded is not None
        assert loaded.id == "job-../../etc/passwd"

    def test_dotdot_in_id(self, store: JobStore) -> None:
        job = _make_job(id="job-..test")
        store.save(job)
        loaded = store.load("job-..test")
        assert loaded is not None
