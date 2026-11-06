import httpx
import pytest

from data_platform.knowledge.chunking import search_tokens, split_text
from data_platform.knowledge.store import KnowledgeStore
from data_platform.model_gateway.jina import JinaEmbeddingProvider


class FixedEmbeddings:
    def _vector(self, text):
        return [1.0, 0.0] if "退款" in text else [0.0, 1.0]

    def passages(self, texts):
        return [self._vector(text) for text in texts]

    def queries(self, texts):
        return [self._vector(text) for text in texts]


def test_chunking_and_chinese_tokens():
    assert split_text("第一段。\n第二段。", size=100, overlap=10) == ["第一段。\n第二段。"]
    tokens = search_tokens("客户退款率")
    assert "退款" in tokens
    assert "客户" in tokens
    with pytest.raises(ValueError):
        split_text("x", size=20)


def test_hybrid_search_scope_sources_and_deletion(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    embed = FixedEmbeddings()
    kb = store.create_base("team-a", "rules")
    other = store.create_base("team-b", "rules")
    doc = store.add_document("team-a", kb, "refund.md", "退款规则：付款后七天内可申请退款。", embed)
    assert store.add_document("team-a", kb, "copy.md", "退款规则：付款后七天内可申请退款。", embed) == doc
    store.add_document("team-a", kb, "sales.md", "营业收入按照已支付金额统计。", embed)
    store.add_document("team-b", other, "secret.md", "退款规则：仅内部可见。", embed)
    hits = store.search("team-a", kb, "退款规则", embed)
    assert hits[0].filename == "refund.md"
    assert hits[0].keyword_rank is not None
    assert hits[0].vector_rank is not None
    assert all("仅内部可见" not in hit.text for hit in hits)
    assert store.list_documents("team-a", kb)[0]["chunks"] == 1
    with pytest.raises(LookupError):
        store.search("team-b", kb, "退款", embed)
    store.delete_document("team-a", kb, doc)
    assert all(hit.document_id != doc for hit in store.search("team-a", kb, "退款", embed))


def test_incompatible_embedding_index_rejected(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    kb = store.create_base("team-a", "rules")
    store.add_document("team-a", kb, "one.md", "退款规则", FixedEmbeddings())

    class OtherEmbeddings(FixedEmbeddings):
        pass

    with pytest.raises(ValueError, match="embedding model"):
        store.add_document("team-a", kb, "two.md", "订单规则", OtherEmbeddings())
    with pytest.raises(ValueError, match="embedding model"):
        store.search("team-a", kb, "退款", OtherEmbeddings())


def test_jina_adapter_uses_distinct_tasks_without_network():
    requests = []

    def handle(request):
        body = __import__("json").loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": [1.0] * 32} for i in range(len(body["input"]))
        ]})

    provider = JinaEmbeddingProvider(api_key="test-only", dimensions=32,
                                     client=httpx.Client(transport=httpx.MockTransport(handle)))
    assert len(provider.passages(["document"])[0]) == 32
    assert len(provider.queries(["question"])[0]) == 32
    assert [request["task"] for request in requests] == ["retrieval.passage", "retrieval.query"]
