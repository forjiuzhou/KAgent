"""Plan — first-class persistent object for knowledge base change proposals.

A Plan captures the agent's intention to modify the knowledge base,
expressed as natural language rather than executable tool calls.

Lifecycle:
    pending → approved → executed
    pending → rejected
    pending → expired  (target files changed, or time limit exceeded)

Storage: .meta/plans/<plan_id>.json
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class PlanStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    PARTIALLY_EXECUTED = "partially_executed"


class PlanIntent(Enum):
    APPEND = "append"
    CREATE = "create"
    ORGANIZE = "organize"
    RESTRUCTURE = "restructure"


class ChangeType(Enum):
    INCREMENTAL = "incremental"
    STRUCTURAL = "structural"


@dataclass
class Plan:
    id: str
    status: PlanStatus
    created_at: str
    updated_at: str
    summary: str
    targets: list[str]
    rationale: str
    intent: str
    change_type: str
    open_questions: list[str] = field(default_factory=list)
    source_session_id: str | None = None
    target_mtimes: dict[str, float] = field(default_factory=dict)
    execution_report: str | None = None
    expires_hours: float = 24.0

    def is_expired(self) -> bool:
        created = datetime.fromisoformat(self.created_at)
        age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        return age_hours > self.expires_hours

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Plan:
        d = dict(d)
        raw_status = d.pop("status", "pending")
        try:
            status = PlanStatus(raw_status)
        except ValueError:
            status = PlanStatus.PENDING
        return cls(status=status, **d)


def generate_plan_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    return f"plan-{ts}-{suffix}"


class PlanStore:
    """Manages Plan persistence under .meta/plans/."""

    def __init__(self, meta_dir: Path) -> None:
        self._dir = meta_dir / "plans"

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, plan_id: str) -> Path:
        safe_id = plan_id.replace("/", "_").replace("..", "_")
        return self._dir / f"{safe_id}.json"

    def save(self, plan: Plan) -> Path:
        self._ensure_dir()
        plan.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path = self._path_for(plan.id)
        path.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load(self, plan_id: str) -> Plan | None:
        path = self._path_for(plan_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Plan.from_dict(data)
        except (json.JSONDecodeError, OSError, TypeError, KeyError):
            return None

    def list_pending(self) -> list[Plan]:
        return [p for p in self._list_all() if p.status == PlanStatus.PENDING]

    def list_all(self, limit: int = 50) -> list[Plan]:
        plans = self._list_all()
        plans.sort(key=lambda p: p.created_at, reverse=True)
        return plans[:limit]

    def _list_all(self) -> list[Plan]:
        if not self._dir.is_dir():
            return []
        plans: list[Plan] = []
        for f in self._dir.glob("plan-*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                plans.append(Plan.from_dict(data))
            except (json.JSONDecodeError, OSError, TypeError, KeyError):
                continue
        return plans

    def update_status(
        self,
        plan_id: str,
        status: PlanStatus,
        **fields: Any,
    ) -> Plan | None:
        plan = self.load(plan_id)
        if plan is None:
            return None
        plan.status = status
        for k, v in fields.items():
            if hasattr(plan, k):
                setattr(plan, k, v)
        self.save(plan)
        return plan

    def check_staleness(self, plan: Plan) -> list[str]:
        """Return list of target files whose mtime changed since plan creation."""
        changed: list[str] = []
        for target, recorded_mtime in plan.target_mtimes.items():
            try:
                current_mtime = os.path.getmtime(target)
                if abs(current_mtime - recorded_mtime) > 0.5:
                    changed.append(target)
            except OSError:
                pass
        return changed

    def delete(self, plan_id: str) -> bool:
        path = self._path_for(plan_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    def migrate_legacy_pending(self, meta_dir: Path) -> Plan | None:
        """Convert old pending-organize.json to a Plan object.

        Returns the migrated Plan if the legacy file existed, else None.
        After migration the old file is removed.
        """
        legacy_path = meta_dir / "pending-organize.json"
        if not legacy_path.is_file():
            return None

        try:
            actions = json.loads(legacy_path.read_text(encoding="utf-8"))
            if not isinstance(actions, list):
                legacy_path.unlink()
                return None
        except (json.JSONDecodeError, OSError):
            legacy_path.unlink()
            return None

        action_names = [a.get("name", "") for a in actions]
        targets = []
        for a in actions:
            args = a.get("arguments", {})
            t = args.get("path", "") or args.get("target", "")
            if t:
                targets.append(t)

        summary_parts = []
        for a in actions:
            name = a.get("name", "?")
            args = a.get("arguments", {})
            title = args.get("title", args.get("path", args.get("target", "")))
            summary_parts.append(f"- {name}: {title}")

        plan = Plan(
            id=generate_plan_id(),
            status=PlanStatus.PENDING,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            summary="Migrated from legacy pending-organize.json:\n" + "\n".join(summary_parts),
            targets=targets,
            rationale="Auto-migrated from legacy format",
            intent="organize",
            change_type="structural",
            open_questions=[],
        )

        # Preserve the original actions for backward compatibility
        plan_dict = plan.to_dict()
        plan_dict["_legacy_actions"] = actions
        self._ensure_dir()
        path = self._path_for(plan.id)
        path.write_text(
            json.dumps(plan_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        legacy_path.unlink()
        return plan
