"""Read-only validation for the frozen retrieval-v1 cases and corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path("evaluation/fixtures/retrieval_v1.manifest.json")
EXPECTED_CATEGORIES = {"metric": 12, "schema": 8, "rule": 6, "combo": 6}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases_path = Path(manifest["case_file"])
    corpus_dir = Path(manifest["corpus_dir"])
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    files = sorted(path for path in corpus_dir.iterdir() if path.is_file() and path.suffix.lower() in {".md", ".txt"})
    actual_hashes = {path.name: file_sha256(path) for path in files}
    expected_hashes = {item["filename"]: item["sha256"] for item in manifest["documents"]}
    category_counts = dict(Counter(case.get("category") for case in cases))
    ids = [case.get("id") for case in cases]
    errors: list[str] = []

    if manifest.get("schema_version") != "retrieval-v1":
        errors.append("unexpected schema_version")
    if len(cases) != 32 or manifest.get("case_count") != 32:
        errors.append("retrieval-v1 requires exactly 32 cases")
    if category_counts != EXPECTED_CATEGORIES:
        errors.append(f"unexpected category distribution: {category_counts}")
    if not all(isinstance(case_id, str) and case_id for case_id in ids) or len(set(ids)) != len(ids):
        errors.append("case IDs must be nonempty and unique")
    if len(actual_hashes) != len(files) or len(expected_hashes) != len(manifest["documents"]):
        errors.append("corpus filenames or manifest filenames are duplicated")
    if actual_hashes != expected_hashes:
        errors.append("corpus files or SHA-256 values differ from manifest")
    if file_sha256(cases_path) != manifest.get("dataset_sha256"):
        errors.append("case file SHA-256 differs from manifest")
    if len(set(actual_hashes.values())) != len(actual_hashes):
        errors.append("two corpus documents have identical contents")

    for case in cases:
        case_id = case.get("id", "<missing>")
        query = case.get("query")
        gold = case.get("gold_filename")
        if not isinstance(query, str) or len(query.strip()) < 8:
            errors.append(f"{case_id}: query is missing or too short")
        if not isinstance(gold, str) or gold not in actual_hashes:
            errors.append(f"{case_id}: gold document is missing")
        if not isinstance(case.get("gold_reason"), str) or not case["gold_reason"].strip():
            errors.append(f"{case_id}: gold_reason is missing")
        if isinstance(query, str) and isinstance(gold, str) and gold in actual_hashes:
            text = (corpus_dir / gold).read_text(encoding="utf-8")
            if query.strip() in text:
                errors.append(f"{case_id}: exact question text leaked into gold document")

    return {
        "schema_version": manifest.get("schema_version"),
        "case_count": len(cases),
        "category_counts": category_counts,
        "corpus_document_count": len(files),
        "dataset_sha256_matches": file_sha256(cases_path) == manifest.get("dataset_sha256"),
        "corpus_sha256_matches": actual_hashes == expected_hashes,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.manifest)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        if args.output.exists():
            raise FileExistsError(f"refusing to overwrite {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
