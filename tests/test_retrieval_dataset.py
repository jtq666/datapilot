from evaluation.validate_retrieval_dataset import validate


def test_frozen_retrieval_assets_match_manifest() -> None:
    result = validate()
    assert result["errors"] == []
    assert result["case_count"] == 32
    assert result["category_counts"] == {"metric": 12, "schema": 8, "rule": 6, "combo": 6}
    assert result["corpus_document_count"] == 19
    assert result["dataset_sha256_matches"] is True
    assert result["corpus_sha256_matches"] is True
