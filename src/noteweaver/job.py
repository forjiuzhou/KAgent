"""Job — persistent model for goal-driven background tasks.

Jobs represent long-running, multi-iteration tasks that the agent can
execute in the background (unattended).  A job goes through a
generator/evaluator loop until acceptance criteria are met.

Lifecycle:
    draft → ready → running ↔ evaluating → completed
    Any active state → paused / blocked / failed / cancelled

Storage layout::

    .meta/jobs/
      job-20260414-a3f2.json          # Job object
      job-20260414-a3f2/              # Per-step records
        step-001.json
        step-002.json
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------

class JobStatus(Enum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    EVALUATING = "evaluating"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES = frozenset({
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
})

_VALID_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.DRAFT: frozenset({JobStatus.READY, JobStatus.CANCELLED}),
    JobStatus.READY: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({
        JobStatus.EVALUATING, JobStatus.PAUSED,
        JobStatus.BLOCKED, JobStatus.FAILED, JobStatus.CANCELLED,
    }),
    JobStatus.EVALUATING: frozenset({
        JobStatus.RUNNING, JobStatus.COMPLETED,
        JobStatus.BLOCKED, JobStatus.FAILED, JobStatus.CANCELLED,
    }),
    JobStatus.PAUSED: frozenset({JobStatus.READY, JobStatus.CANCELLED}),
    JobStatus.BLOCKED: frozenset({JobStatus.READY, JobStatus.CANCELLED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


def _is_valid_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in _VALID_TRANSITIONS.get(current, frozenset())


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclass
class WriteScope:
    """Constrains what a running job is allowed to write."""
    allowed_path_prefixes: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    max_pages: int = 50

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WriteScope:
        return cls(
            allowed_path_prefixes=list(d.get("allowed_path_prefixes", [])),
            allowed_tools=list(d.get("allowed_tools", [])),
            max_pages=int(d.get("max_pages", 50)),
        )


@dataclass
class StepRecord:
    """One iteration of the generator/evaluator loop."""
    iteration: int
    started_at: str
    completed_at: str
    actions_taken: list[str] = field(default_factory=list)
    structural_check: dict[str, Any] = field(default_factory=dict)
    evaluation: str | None = None
    evaluation_passed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StepRecord:
        return cls(
            iteration=int(d["iteration"]),
            started_at=d["started_at"],
            completed_at=d["completed_at"],
            actions_taken=list(d.get("actions_taken", [])),
            structural_check=dict(d.get("structural_check", {})),
            evaluation=d.get("evaluation"),
            evaluation_passed=d.get("evaluation_passed"),
        )


@dataclass
class Job:
    """A goal-driven background task with acceptance criteria."""
    id: str
    status: JobStatus
    created_at: str
    updated_at: str

    # Contract
    goal: str
    acceptance_criteria: list[str] = field(default_factory=list)
    evaluator_prompt: str = ""
    write_scope: WriteScope = field(default_factory=WriteScope)
    max_iterations: int = 10

    # Progress
    iteration_count: int = 0
    progress: list[StepRecord] = field(default_factory=list)
    last_evaluation: str | None = None
    blocked_reason: str | None = None

    # Runtime
    generator_context: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["write_scope"] = self.write_scope.to_dict()
        d["progress"] = [s.to_dict() for s in self.progress]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Job:
        d = dict(d)
        raw_status = d.pop("status", "draft")
        try:
            status = JobStatus(raw_status)
        except ValueError:
            status = JobStatus.DRAFT

        raw_scope = d.pop("write_scope", {})
        write_scope = WriteScope.from_dict(raw_scope) if raw_scope else WriteScope()

        raw_progress = d.pop("progress", [])
        progress = [StepRecord.from_dict(s) for s in raw_progress]

        return cls(
            status=status,
            write_scope=write_scope,
            progress=progress,
            **d,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in (
            JobStatus.READY, JobStatus.RUNNING, JobStatus.EVALUATING,
        )


# ------------------------------------------------------------------
# ID generation
# ------------------------------------------------------------------

def generate_job_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = secrets.token_hex(2)
    return f"job-{ts}-{suffix}"


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------

class JobStore:
    """Manages Job persistence under ``.meta/jobs/``."""

    def __init__(self, meta_dir: Path) -> None:
        self._dir = meta_dir / "jobs"

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, job_id: str) -> Path:
        safe = job_id.replace("/", "_").replace("..", "_")
        return self._dir / f"{safe}.json"

    def _step_dir_for(self, job_id: str) -> Path:
        safe = job_id.replace("/", "_").replace("..", "_")
        return self._dir / safe

    # ---- CRUD ----

    def save(self, job: Job) -> Path:
        self._ensure_dir()
        job.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path = self._path_for(job.id)
        path.write_text(
            json.dumps(job.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load(self, job_id: str) -> Job | None:
        path = self._path_for(job_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Job.from_dict(data)
        except (json.JSONDecodeError, OSError, TypeError, KeyError):
            return None

    def delete(self, job_id: str) -> bool:
        path = self._path_for(job_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    # ---- Queries ----

    def list_all(self, limit: int = 50) -> list[Job]:
        jobs = self._load_all()
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def list_by_status(self, *statuses: JobStatus) -> list[Job]:
        status_set = set(statuses)
        return [j for j in self._load_all() if j.status in status_set]

    def next_ready(self) -> Job | None:
        """Return the oldest READY job, or ``None`` if a job is already RUNNING/EVALUATING."""
        all_jobs = self._load_all()
        if any(j.status in (JobStatus.RUNNING, JobStatus.EVALUATING) for j in all_jobs):
            return None
        ready = [j for j in all_jobs if j.status == JobStatus.READY]
        if not ready:
            return None
        ready.sort(key=lambda j: j.created_at)
        return ready[0]

    # ---- Status transitions ----

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        **fields: Any,
    ) -> Job | None:
        """Transition a job to a new status with optional field updates.

        Returns the updated Job, or ``None`` if the job was not found
        or the transition is invalid.
        """
        job = self.load(job_id)
        if job is None:
            return None
        if not _is_valid_transition(job.status, status):
            return None
        job.status = status
        for k, v in fields.items():
            if hasattr(job, k):
                setattr(job, k, v)
        self.save(job)
        return job

    # ---- Step records ----

    def append_step(self, job_id: str, step: StepRecord) -> Path | None:
        """Persist a step record and update the job's progress list."""
        job = self.load(job_id)
        if job is None:
            return None

        job.progress.append(step)
        job.iteration_count = len(job.progress)
        if step.evaluation is not None:
            job.last_evaluation = step.evaluation
        self.save(job)

        step_dir = self._step_dir_for(job_id)
        step_dir.mkdir(parents=True, exist_ok=True)
        step_path = step_dir / f"step-{step.iteration:03d}.json"
        step_path.write_text(
            json.dumps(step.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return step_path

    def load_step(self, job_id: str, iteration: int) -> StepRecord | None:
        step_path = self._step_dir_for(job_id) / f"step-{iteration:03d}.json"
        if not step_path.is_file():
            return None
        try:
            data = json.loads(step_path.read_text(encoding="utf-8"))
            return StepRecord.from_dict(data)
        except (json.JSONDecodeError, OSError, TypeError, KeyError):
            return None

    # ---- Internal helpers ----

    def _load_all(self) -> list[Job]:
        if not self._dir.is_dir():
            return []
        jobs: list[Job] = []
        for f in self._dir.glob("job-*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                jobs.append(Job.from_dict(data))
            except (json.JSONDecodeError, OSError, TypeError, KeyError):
                continue
        return jobs
