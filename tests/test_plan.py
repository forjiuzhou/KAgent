"""Tests for the Plan data model and PlanStore."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from noteweaver.plan import (
    Plan,
    PlanStatus,
    PlanStore,
    generate_plan_id,
)


@pytest.fixture
def meta_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".meta"
    d.mkdir()
    return d


@pytest.fixture
def store(meta_dir: Path) -> PlanStore:
    return PlanStore(meta_dir)


def _make_plan(**overrides) -> Plan:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    defaults = dict(
        id=generate_plan_id(),
        status=PlanStatus.PENDING,
        created_at=now,
        updated_at=now,
        summary="Test plan",
        targets=["wiki/concepts/test.md"],
        rationale="Test rationale",
        intent="create",
        change_type="structural",
    )
    defaults.update(overrides)
    return Plan(**defaults)


class TestPlanId:
    def test_format(self) -> None:
        pid = generate_plan_id()
        assert pid.startswith("plan-")
        parts = pid.split("-")
        assert len(parts) == 4

    def test_uniqueness(self) -> None:
        ids = {generate_plan_id() for _ in range(20)}
        assert len(ids) == 20


class TestPlanSerialization:
    def test_to_dict(self) -> None:
        plan = _make_plan()
        d = plan.to_dict()
        assert d["status"] == "pending"
        assert d["intent"] == "create"

    def test_from_dict(self) -> None:
        plan = _make_plan()
        d = plan.to_dict()
        restored = Plan.from_dict(d)
        assert restored.id == plan.id
        assert restored.status == PlanStatus.PENDING
        assert restored.summary == "Test plan"

    def test_roundtrip(self) -> None:
        plan = _make_plan(
            open_questions=["Is this the right page?"],
            target_mtimes={"wiki/concepts/test.md": 12345.0},
        )
        d = plan.to_dict()
        restored = Plan.from_dict(d)
        assert restored.open_questions == ["Is this the right page?"]
        assert restored.target_mtimes["wiki/concepts/test.md"] == 12345.0


class TestPlanExpiry:
    def test_not_expired(self) -> None:
        plan = _make_plan()
        assert not plan.is_expired()

    def test_expired(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(
            timespec="seconds"
        )
        plan = _make_plan(created_at=old)
        assert plan.is_expired()

    def test_custom_expiry(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(
            timespec="seconds"
        )
        plan = _make_plan(created_at=old, expires_hours=1.0)
        assert plan.is_expired()

        plan2 = _make_plan(created_at=old, expires_hours=3.0)
        assert not plan2.is_expired()


class TestPlanStore:
    def test_save_and_load(self, store: PlanStore) -> None:
        plan = _make_plan()
        store.save(plan)
        loaded = store.load(plan.id)
        assert loaded is not None
        assert loaded.id == plan.id
        assert loaded.status == PlanStatus.PENDING

    def test_load_nonexistent(self, store: PlanStore) -> None:
        assert store.load("plan-nonexistent") is None

    def test_list_pending(self, store: PlanStore) -> None:
        p1 = _make_plan(id="plan-001")
        p2 = _make_plan(id="plan-002", status=PlanStatus.EXECUTED)
        p3 = _make_plan(id="plan-003")
        store.save(p1)
        store.save(p2)
        store.save(p3)
        pending = store.list_pending()
        ids = {p.id for p in pending}
        assert "plan-001" in ids
        assert "plan-003" in ids
        assert "plan-002" not in ids

    def test_list_all(self, store: PlanStore) -> None:
        for i in range(5):
            store.save(_make_plan(id=f"plan-{i:03d}"))
        assert len(store.list_all()) == 5

    def test_update_status(self, store: PlanStore) -> None:
        plan = _make_plan()
        store.save(plan)
        updated = store.update_status(plan.id, PlanStatus.APPROVED)
        assert updated is not None
        assert updated.status == PlanStatus.APPROVED
        loaded = store.load(plan.id)
        assert loaded.status == PlanStatus.APPROVED

    def test_update_with_extra_fields(self, store: PlanStore) -> None:
        plan = _make_plan()
        store.save(plan)
        store.update_status(
            plan.id,
            PlanStatus.EXECUTED,
            execution_report="Done: 3 operations",
        )
        loaded = store.load(plan.id)
        assert loaded.status == PlanStatus.EXECUTED
        assert loaded.execution_report == "Done: 3 operations"

    def test_delete(self, store: PlanStore) -> None:
        plan = _make_plan()
        store.save(plan)
        assert store.delete(plan.id)
        assert store.load(plan.id) is None

    def test_delete_nonexistent(self, store: PlanStore) -> None:
        assert not store.delete("plan-nope")


class TestStalenessCheck:
    def test_no_targets(self, store: PlanStore) -> None:
        plan = _make_plan(target_mtimes={})
        assert store.check_staleness(plan) == []

    def test_unchanged_file(self, store: PlanStore, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("hello")
        mtime = f.stat().st_mtime
        plan = _make_plan(target_mtimes={str(f): mtime})
        assert store.check_staleness(plan) == []

    def test_changed_file(self, store: PlanStore, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("hello")
        old_mtime = f.stat().st_mtime - 10
        plan = _make_plan(target_mtimes={str(f): old_mtime})
        stale = store.check_staleness(plan)
        assert str(f) in stale


class TestLegacyMigration:
    def test_migrate_pending_organize(self, meta_dir: Path) -> None:
        store = PlanStore(meta_dir)
        legacy = [
            {"name": "capture", "arguments": {"title": "X", "content": "Y"}},
            {"name": "write_page", "arguments": {"path": "wiki/concepts/x.md", "content": "..."}},
        ]
        legacy_path = meta_dir / "pending-organize.json"
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")

        plan = store.migrate_legacy_pending(meta_dir)
        assert plan is not None
        assert plan.status == PlanStatus.PENDING
        assert "capture" in plan.summary
        assert not legacy_path.exists()

    def test_migrate_no_legacy_file(self, meta_dir: Path) -> None:
        store = PlanStore(meta_dir)
        assert store.migrate_legacy_pending(meta_dir) is None


class TestClassifyChangeType:
    def test_create_always_structural(self) -> None:
        from noteweaver.tools.policy import classify_change_type
        assert classify_change_type("create", [], "incremental") == "structural"

    def test_restructure_always_structural(self) -> None:
        from noteweaver.tools.policy import classify_change_type
        assert classify_change_type("restructure", [], "incremental") == "structural"

    def test_many_targets_structural(self) -> None:
        from noteweaver.tools.policy import classify_change_type
        targets = ["a.md", "b.md", "c.md"]
        assert classify_change_type("append", targets, "incremental") == "structural"

    def test_append_existing_trusts_model(self, tmp_path: Path) -> None:
        from noteweaver.tools.policy import classify_change_type
        from noteweaver.vault import Vault
        v = Vault(tmp_path, auto_git=False)
        v.init()
        v.write_file("wiki/concepts/test.md", "---\ntitle: T\n---\n# T")
        result = classify_change_type(
            "append", ["wiki/concepts/test.md"], "incremental", vault=v,
        )
        assert result == "incremental"

    def test_append_nonexistent_structural(self, tmp_path: Path) -> None:
        from noteweaver.tools.policy import classify_change_type
        from noteweaver.vault import Vault
        v = Vault(tmp_path, auto_git=False)
        v.init()
        result = classify_change_type(
            "append", ["wiki/concepts/nope.md"], "incremental", vault=v,
        )
        assert result == "structural"
