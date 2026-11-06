import pytest

from data_platform.workflows.catalog import WorkflowCatalog
from data_platform.workflows.spec import Edge, Node, WorkflowSpec


def test_versions_are_immutable_and_workspace_scoped(tmp_path):
    catalog = WorkflowCatalog(tmp_path / "catalog.db")
    first = WorkflowSpec("set", (Node("set", "set", {"output_key": "x", "value": 1}), Node("end", "end")),
                         (Edge("set", "end"),))
    second = WorkflowSpec("set", (Node("set", "set", {"output_key": "x", "value": 2}), Node("end", "end")),
                          (Edge("set", "end"),))
    workflow_id = catalog.create("team-a", "daily report", first)
    assert catalog.get("team-a", workflow_id)[0] == 1
    assert catalog.update("team-a", workflow_id, second, 1) == 2
    assert catalog.get("team-a", workflow_id, 1)[1] == first
    assert catalog.get("team-a", workflow_id)[1] == second
    assert catalog.list_for_workspace("team-a")[0]["version"] == 2
    assert catalog.list_for_workspace("team-b") == []
    with pytest.raises(LookupError):
        catalog.get("team-b", workflow_id)
    with pytest.raises(ValueError, match="version conflict"):
        catalog.update("team-a", workflow_id, first, 1)
