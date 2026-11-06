"""Run frozen business cases through the local Ollama + Qwen AgentRunner.

The model endpoint is read from ``OLLAMA_BASE_URL`` and the model tag from
``OLLAMA_MODEL``. Results stay local and no secret is written to output files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from data_platform.agents.runner import AgentRunner
from data_platform.data_sources.agent_tools import create_sqlite_tools
from data_platform.data_sources.sqlite_source import SQLiteDataSource
from data_platform.model_gateway.deepseek import (
    OpenAICompatibleChatProvider,
    create_default_chat_provider,
)
from data_platform.runtime.models import RunStatus
from data_platform.runtime.store import RunStore


SYSTEM_PROMPT = """You are an enterprise data analyst.
Use database_schema before writing SQL when the schema is not already clear.
Use query_database for every numeric claim. Never invent values or use write SQL.
After all required tool calls, return JSON only, without markdown fences:
{"answer":"short Chinese explanation", "columns":[...], "rows":[[...]], "evidence":["tool result description"]}
The columns and rows must be the final result that directly answers the user's question.
Do not include alternative calculations or extra comparison rows. Preserve the
number of columns and rows returned by the final query; column names may be
translated in the explanation but values must remain unchanged.
"""


def load_cases(path: Path, start: int, limit: int | None) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = cases[start:]
    return selected[:limit] if limit else selected


def file_sha256(path: Path) -> str:
    """Hash frozen inputs and outputs without loading a database into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_summary(
    *, results: list[dict[str, Any]], cases_path: Path, database_path: Path,
    output_path: Path, start: int, full_case_count: int, model: str,
    started_at_utc: str, elapsed_ms: int,
) -> dict[str, Any]:
    """Describe the exact run behind a score; subsets are never labeled full-set results."""
    selected_ids = [item["id"] for item in results]
    return {
        "evaluation": "business-v2",
        "mode": "full_dataset" if start == 0 and len(results) == full_case_count else "subset",
        "started_at_utc": started_at_utc,
        "python_version": platform.python_version(),
        "model_provider": "ollama",
        "model": model,
        "max_turns": 8,
        "cases_file": str(cases_path),
        "cases_sha256": file_sha256(cases_path),
        "database_file": str(database_path),
        "database_sha256": file_sha256(database_path),
        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "script_sha256": file_sha256(Path(__file__)),
        "result_file": str(output_path),
        "result_sha256": file_sha256(output_path),
        "full_case_count": full_case_count,
        "cases": len(results),
        "selected_ids": selected_ids,
        "task_success": sum(bool(item["task_success"]) for item in results),
        "sql_result_correct": sum(bool(item["sql_result_correct"]) for item in results),
        "final_answer_correct": sum(bool(item["final_answer_correct"]) for item in results),
        "failed_ids": [item["id"] for item in results if not item["task_success"]],
        "elapsed_ms": elapsed_ms,
    }


def parse_answer(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def _value_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) <= 1e-6
    return actual == expected


def _row_equal(actual: list[Any], expected: list[Any]) -> bool:
    return len(actual) == len(expected) and all(
        _value_equal(left, right) for left, right in zip(actual, expected)
    )


def _unordered_rows_match(actual: list[list[Any]], expected: list[list[Any]]) -> bool:
    remaining = list(actual)
    for expected_row in expected:
        match_index = next(
            (index for index, actual_row in enumerate(remaining)
             if _row_equal(actual_row, expected_row)),
            None,
        )
        if match_index is None:
            return False
        remaining.pop(match_index)
    return not remaining


def _order_is_required(case: dict[str, Any]) -> bool:
    if "order_sensitive" in case:
        return bool(case["order_sensitive"])
    question = case.get("question", "")
    return any(token in question for token in ("排名", "排序", "前", "最高", "最低"))


def _project_rows(actual: dict[str, Any], expected: dict[str, Any]) -> list[list[Any]] | None:
    actual_columns = actual.get("columns")
    actual_rows = actual.get("rows")
    expected_columns = expected.get("columns")
    if not isinstance(actual_columns, list) or not isinstance(actual_rows, list):
        return None
    if not isinstance(expected_columns, list):
        return None

    # Prefer named projection when the model kept the gold aliases. This allows
    # harmless extra columns while still checking the requested values.
    positions = [actual_columns.index(column) for column in expected_columns
                 if column in actual_columns]
    if len(positions) == len(expected_columns):
        return [[row[position] for position in positions] for row in actual_rows]

    # Models often translate aliases (for example name -> 客户分群). When names
    # differ, a same-width result is compared positionally. For a single-column
    # request, an extra diagnostic column is allowed only when the expected value
    # appears in every returned row; the final answer still must contain it.
    if len(actual_columns) == len(expected_columns):
        return [list(row) for row in actual_rows]
    if len(expected_columns) == 1:
        expected_rows = expected.get("rows")
        if not isinstance(expected_rows, list) or len(actual_rows) != len(expected_rows):
            return None
        for position in range(len(actual_columns)):
            candidate = [[row[position]] for row in actual_rows]
            if _unordered_rows_match(candidate, expected_rows):
                return candidate
        return None
    return None


def rows_match(actual: Any, expected: dict[str, Any], *, order_required: bool = False) -> bool:
    if not isinstance(actual, dict):
        return False
    projected = _project_rows(actual, expected)
    expected_rows = expected.get("rows")
    if projected is None or not isinstance(expected_rows, list):
        return False
    if order_required:
        return len(projected) == len(expected_rows) and all(
            _row_equal(left, right) for left, right in zip(projected, expected_rows)
        )
    return _unordered_rows_match(projected, expected_rows)


def tool_results(state: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for message in state.get("messages", []):
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message.get("content", ""))
        except (TypeError, json.JSONDecodeError):
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and isinstance(data.get("columns"), list) and isinstance(data.get("rows"), list):
            results.append(data)
    return results


def run_case(case: dict[str, Any], source: SQLiteDataSource, provider: OpenAICompatibleChatProvider,
             runs_path: Path) -> dict[str, Any]:
    run_store = RunStore(runs_path)
    tools = create_sqlite_tools(source)
    run_id = AgentRunner(run_store, provider, tools, max_turns=8).start(
        "evaluation", case["question"], system_prompt=SYSTEM_PROMPT,
    )
    run = run_store.get_run(run_id, "evaluation")
    checkpoint = run_store.load_checkpoint(run_id, "evaluation")
    state = checkpoint[1] if checkpoint else {}
    final_text = state.get("answer") or ""
    parsed = parse_answer(final_text)
    expected = case["gold_result"]
    tool_data = tool_results(state)
    order_required = _order_is_required(case)
    tool_correct = any(rows_match(data, expected, order_required=order_required) for data in tool_data)
    final_correct = rows_match(parsed, expected, order_required=order_required)
    return {
        "id": case["id"],
        "category": case["category"],
        "difficulty": case["difficulty"],
        "status": run.status.value if run else "missing",
        "turns": state.get("turns"),
        "tool_calls": sum(1 for message in state.get("messages", []) if message.get("role") == "assistant" and message.get("tool_calls")),
        "sql_result_correct": tool_correct,
        "final_answer_correct": final_correct,
        "answer_structured": parsed is not None and bool(parsed.get("answer")),
        "task_success": bool(run and run.status == RunStatus.SUCCEEDED and tool_correct and final_correct and parsed.get("answer")),
        "order_required": order_required,
        "tool_results": tool_data,
        "answer": final_text,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("evaluation/datasets/business_cases_v2.jsonl"))
    parser.add_argument("--database", type=Path, default=Path("evaluation/fixtures/orders_eval_v2.db"))
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", type=Path, default=Path("evaluation/results/business_eval_v2.jsonl"))
    args = parser.parse_args()
    if args.start < 0:
        raise SystemExit("--start must be non-negative")
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be positive")
    summary_path = args.output.with_suffix(".summary.json")
    if args.output.exists() or summary_path.exists():
        raise SystemExit("refusing to overwrite an existing evaluation result or summary")
    full_case_count = len(load_cases(args.cases, 0, None))
    cases = load_cases(args.cases, args.start, args.limit)
    if not cases:
        raise SystemExit("selected business case range is empty")
    source = SQLiteDataSource(args.database)
    provider = create_default_chat_provider()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    started_at_utc = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    try:
        with TemporaryDirectory(prefix="agent-runs-", ignore_cleanup_errors=True) as directory:
            for case in cases:
                result = run_case(case, source, provider, Path(directory) / f"{case['id']}.db")
                results.append(result)
                args.output.write_text(
                    "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in results),
                    encoding="utf-8",
                )
                print(json.dumps({key: result[key] for key in ("id", "status", "turns", "tool_calls", "sql_result_correct", "final_answer_correct", "task_success")}, ensure_ascii=False))
    finally:
        provider.close()
    summary = build_summary(
        results=results, cases_path=args.cases, database_path=args.database,
        output_path=args.output, start=args.start, full_case_count=full_case_count,
        model=provider.model, started_at_utc=started_at_utc,
        elapsed_ms=round((time.monotonic() - started) * 1000),
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in (
        "mode", "cases", "task_success", "sql_result_correct", "final_answer_correct", "elapsed_ms"
    )} | {"output": str(args.output), "summary": str(summary_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
