"""Validate the frozen business dataset without calling a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from data_platform.data_sources.sqlite_source import SQLiteDataSource


def validate(fixture_dir: Path) -> dict:
    manifest = json.loads((fixture_dir / "orders_eval_v2.manifest.json").read_text(encoding="utf-8"))
    db_path = fixture_dir / "orders_eval_v2.db"
    dataset_path = fixture_dir.parent / "datasets" / "business_cases_v2.jsonl"
    cases = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    source = SQLiteDataSource(db_path, max_rows=1000)
    errors: list[dict] = []
    for case in cases:
        try:
            result = source.query(case["gold_sql_variants"][0])
            expected = case["gold_result"]
            if result.columns != expected["columns"] or result.rows != expected["rows"]:
                errors.append({"id": case["id"], "reason": "gold result changed"})
        except Exception as exc:  # validation report should identify the case, not abort early
            errors.append({"id": case["id"], "reason": type(exc).__name__})

    with sqlite3.connect(db_path) as db:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]

    actual_db_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    actual_dataset_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    return {
        "schema_version": manifest["schema_version"],
        "case_count": len(cases),
        "category_counts": dict(Counter(case["category"] for case in cases)),
        "difficulty_counts": dict(Counter(case["difficulty"] for case in cases)),
        "table_count": len(tables),
        "tables": tables,
        "gold_sql_executable": len(errors) == 0,
        "errors": errors,
        "database_sha256_matches": actual_db_hash == manifest["database_sha256"],
        "dataset_sha256_matches": actual_dataset_hash == manifest["dataset_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, default=Path("evaluation/fixtures"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/results/dataset_validation.json"))
    args = parser.parse_args()
    report = validate(args.fixture_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not report["gold_sql_executable"] or not report["database_sha256_matches"] or not report["dataset_sha256_matches"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
