import json
import sys

import pytest

from evaluation.run_agent_eval import build_summary, main


def test_business_summary_identifies_subset_and_preserves_input_hashes(tmp_path):
    cases = tmp_path / "cases.jsonl"
    database = tmp_path / "orders.db"
    output = tmp_path / "result.jsonl"
    cases.write_text('{"id":"one"}\n{"id":"two"}\n', encoding="utf-8")
    database.write_bytes(b"fixed database bytes")
    output.write_text(json.dumps({"id": "two", "task_success": False}) + "\n", encoding="utf-8")

    report = build_summary(
        results=[{
            "id": "two", "task_success": False,
            "sql_result_correct": True, "final_answer_correct": False,
        }],
        cases_path=cases, database_path=database, output_path=output,
        start=1, full_case_count=2, model="qwen2.5:14b",
        started_at_utc="2026-01-01T00:00:00+00:00", elapsed_ms=100,
    )

    assert report["mode"] == "subset"
    assert report["cases"] == 1
    assert report["full_case_count"] == 2
    assert report["selected_ids"] == ["two"]
    assert report["task_success"] == 0
    assert report["sql_result_correct"] == 1
    assert report["final_answer_correct"] == 0
    assert report["failed_ids"] == ["two"]
    assert len(report["cases_sha256"]) == 64
    assert len(report["database_sha256"]) == 64
    assert len(report["result_sha256"]) == 64
    assert "api_key" not in report


def test_business_summary_identifies_full_dataset(tmp_path):
    cases = tmp_path / "cases.jsonl"
    database = tmp_path / "orders.db"
    output = tmp_path / "result.jsonl"
    cases.write_text("one\ntwo\n", encoding="utf-8")
    database.write_bytes(b"db")
    output.write_text("one\ntwo\n", encoding="utf-8")
    rows = [{
        "id": case_id, "task_success": True,
        "sql_result_correct": True, "final_answer_correct": True,
    } for case_id in ("one", "two")]

    report = build_summary(
        results=rows, cases_path=cases, database_path=database, output_path=output,
        start=0, full_case_count=2, model="qwen2.5:14b",
        started_at_utc="2026-01-01T00:00:00+00:00", elapsed_ms=100,
    )

    assert report["mode"] == "full_dataset"
    assert report["task_success"] == 2


def test_existing_business_result_is_not_overwritten(tmp_path, monkeypatch):
    output = tmp_path / "existing.jsonl"
    output.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run_agent_eval", "--output", str(output)])

    with pytest.raises(SystemExit, match="refusing to overwrite"):
        main()

    assert output.read_text(encoding="utf-8") == "keep me"
