"""Evaluate the frozen retrieval-v1 corpus with the project's Hybrid RAG store.

The script calls Jina Embeddings and therefore requires JINA_API_KEY. It does not
call the Ollama/Qwen chat model. Run dataset validation before any network call.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from data_platform.knowledge.store import KnowledgeStore
from data_platform.model_gateway.jina import JinaEmbeddingProvider
from evaluation.validate_retrieval_dataset import DEFAULT_MANIFEST, file_sha256, validate


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(manifest_path: Path, output: Path, *, smoke: bool = False) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    validation = validate(manifest_path)
    if validation["errors"]:
        raise ValueError(f"frozen retrieval assets failed validation: {validation['errors']}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = manifest["retrieval_config"]
    corpus_dir = Path(manifest["corpus_dir"])
    files = sorted(corpus_dir / item["filename"] for item in manifest["documents"])
    all_cases = load_cases(Path(manifest["case_file"]))
    selected = all_cases[:1] if smoke else all_cases
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    embeddings = JinaEmbeddingProvider(
        model=config["embedding_model"], dimensions=config["dimensions"]
    )
    rows: list[dict[str, Any]] = []

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Keep the temporary SQLite vector index on the project drive.
        with TemporaryDirectory(prefix="retrieval-v1-", dir=output.parent) as directory:
            store = KnowledgeStore(Path(directory) / "knowledge.db")
            workspace_id = "evaluation"
            kb_id = store.create_base(workspace_id, "retrieval-v1-frozen")
            for path in files:
                store.add_document(
                    workspace_id, kb_id, path.name, path.read_text(encoding="utf-8"), embeddings,
                    chunk_size=config["chunk_size"], overlap=config["overlap"],
                )
            for case in selected:
                case_started = time.monotonic()
                hits = store.search(
                    workspace_id, kb_id, case["query"], embeddings,
                    top_k=config["top_k"], candidate_k=config["candidate_k"],
                )
                rank = next(
                    (index for index, hit in enumerate(hits, start=1)
                     if hit.filename == case["gold_filename"]),
                    None,
                )
                rows.append({
                    "id": case["id"],
                    "category": case["category"],
                    "query": case["query"],
                    "gold_filename": case["gold_filename"],
                    "gold_reason": case["gold_reason"],
                    "rank": rank,
                    "hit_at_5": rank is not None,
                    "elapsed_ms": round((time.monotonic() - case_started) * 1000),
                    "top5": [
                        {
                            "filename": hit.filename,
                            "ordinal": hit.ordinal,
                            "chunk_id": hit.chunk_id,
                            "keyword_rank": hit.keyword_rank,
                            "vector_rank": hit.vector_rank,
                            "score": hit.score,
                        }
                        for hit in hits
                    ],
                })
    finally:
        embeddings.close()

    count = len(rows)
    hits_count = sum(row["hit_at_5"] for row in rows)
    result = {
        "evaluation": "retrieval-v1",
        "mode": "smoke_not_reportable" if smoke else "full_32",
        "started_at_utc": started_at,
        "python_version": platform.python_version(),
        "manifest_sha256": file_sha256(manifest_path),
        "dataset_sha256": manifest["dataset_sha256"],
        "retrieval_config": config,
        "corpus_document_count": len(files),
        "cases": count,
        "hit_count": hits_count,
        "recall_at_5": hits_count / count,
        "mrr_at_5": sum(1 / row["rank"] for row in rows if row["rank"] is not None) / count,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "details": rows,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    result = run(args.manifest, args.output, smoke=args.smoke)
    print(json.dumps({key: result[key] for key in (
        "mode", "cases", "hit_count", "recall_at_5", "mrr_at_5", "elapsed_ms"
    )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
