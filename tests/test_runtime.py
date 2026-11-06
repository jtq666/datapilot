import pytest

from data_platform.runtime.models import RunEvent, RunStatus, utcnow
from data_platform.runtime.store import RunStore


def test_run_event_and_checkpoint_survive_reopen(tmp_path):
    path = tmp_path / "runs.db"
    store = RunStore(path)
    run = store.create_run("run-1", "space-1", "agent")
    assert run.version == 0
    event = store.append_event(RunEvent("evt-1", "run-1", "step-1", "tool-started", utcnow(), {"tool": "sql"}), "space-1")
    assert event.sequence == 1
    assert store.save_checkpoint("run-1", "space-1", {"step": "step-1"}, 0) == 1
    reopened = RunStore(path)
    assert reopened.get_run("run-1", "space-1").version == 1
    assert reopened.events("run-1", "space-1") == [event]
    assert reopened.load_checkpoint("run-1", "space-1") == (1, {"step": "step-1"})


def test_workspace_isolation_and_version_conflict(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    store.create_run("run-1", "space-1", "workflow")
    assert store.get_run("run-1", "space-2") is None
    with pytest.raises(LookupError):
        store.events("run-1", "space-2")
    with pytest.raises(LookupError):
        store.append_event(RunEvent("evt-1", "run-1", "step-1", "started", utcnow(), {}), "space-2")
    store.save_checkpoint("run-1", "space-1", {"step": 1}, 0)
    with pytest.raises(ValueError, match="version conflict"):
        store.save_checkpoint("run-1", "space-1", {"step": 2}, 0)


def test_duplicate_event_rejected(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    store.create_run("run-1", "space-1", "agent")
    event = RunEvent("evt-1", "run-1", "step-1", "started", utcnow(), {})
    store.append_event(event, "space-1")
    with pytest.raises(ValueError, match="duplicate"):
        store.append_event(event, "space-1")


def test_wait_resume_complete_and_audit(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    store.create_run("run-1", "space-1", "workflow")
    assert store.save_checkpoint("run-1", "space-1", {"node": "approval"}, 0) == 1
    waiting = store.transition("run-1", "space-1", 1, RunStatus.WAITING, reason="human approval")
    assert waiting.version == 2
    assert store.load_checkpoint("run-1", "space-1") == (1, {"node": "approval"})
    resumed = store.transition("run-1", "space-1", 2, RunStatus.RUNNING)
    assert resumed.version == 3
    done = store.transition("run-1", "space-1", 3, RunStatus.SUCCEEDED)
    assert done.status == RunStatus.SUCCEEDED
    assert [event.payload["to"] for event in store.events("run-1", "space-1")] == [
        "waiting", "running", "succeeded"
    ]
    with pytest.raises(ValueError, match="invalid run transition"):
        store.transition("run-1", "space-1", 4, RunStatus.RUNNING)
    with pytest.raises(ValueError, match="finished"):
        store.append_event(RunEvent("late", "run-1", "step-1", "tool-result", utcnow(), {}), "space-1")


def test_failed_run_recovery_needs_checkpoint(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    store.create_run("without", "space-1", "agent")
    store.transition("without", "space-1", 0, RunStatus.FAILED)
    with pytest.raises(ValueError, match="without a checkpoint"):
        store.transition("without", "space-1", 1, RunStatus.RUNNING)
    store.create_run("with", "space-1", "agent")
    store.save_checkpoint("with", "space-1", {"tool_calls": 2}, 0)
    store.transition("with", "space-1", 1, RunStatus.FAILED)
    assert store.transition("with", "space-1", 2, RunStatus.RUNNING).status == RunStatus.RUNNING


def test_stale_transition_does_not_create_event(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    store.create_run("run-1", "space-1", "agent")
    store.transition("run-1", "space-1", 0, RunStatus.WAITING)
    with pytest.raises(ValueError, match="version conflict"):
        store.transition("run-1", "space-1", 0, RunStatus.FAILED)
    assert len(store.events("run-1", "space-1")) == 1


def test_run_listing_paginates_without_crossing_workspaces(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    for index in range(4):
        store.create_run(f"run-{index}", "space-1", "workflow")
    store.create_run("other", "space-2", "agent")
    first = store.list_runs("space-1", limit=2)
    second = store.list_runs("space-1", limit=2, offset=2)
    assert len(first) == len(second) == 2
    assert {item.id for item in first + second} == {f"run-{index}" for index in range(4)}
    with pytest.raises(ValueError, match="offset"):
        store.list_runs("space-1", offset=-1)

