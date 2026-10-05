"""Knowledge base unit tests: chunking / ingestion / semantic search / tool registration.

Uses a **fake embedder**, no real model download -- unit tests must be instant and offline.
Retrieval quality with the real model is verified by the repo-root end-to-end scripts.

NOTE: the Chinese strings below are test data -- do not translate.
"""

import asyncio

import numpy as np
import pytest


class FakeEmbedder:
    """Deterministic fake embedding: orthogonal directions per keyword, to make ordering assertable."""

    dim = 4

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(4, dtype="float32")
            if "密码" in t:
                v[0] = 1
            if "退款" in t:
                v[1] = 1
            if "发票" in t:
                v[2] = 1
            if "天气" in t:
                v[3] = 1
            if v.sum() == 0:
                v[3] = 0.5
            out.append(v / (np.linalg.norm(v) or 1))
        return np.stack(out)


@pytest.fixture
def kb(tmp_path, monkeypatch):
    import knowledge

    monkeypatch.setattr(knowledge, "DB_PATH", tmp_path / "kb.db")
    knowledge.init_db()
    return knowledge


# ---------------------------------------------------------------- chunking
def test_chunk_text_splits_and_keeps_content():
    import knowledge

    chunks = knowledge.chunk_text("第一句。第二句。第三句。" * 10, size=20, overlap=5)
    assert len(chunks) > 1
    assert all(c.strip() for c in chunks)


def test_chunk_text_empty():
    import knowledge

    assert knowledge.chunk_text("") == []
    assert knowledge.chunk_text("   ") == []


# ---------------------------------------------------------------- ingest + search
def test_ingest_and_search_ranking(kb):
    kb.ingest("忘记密码可以点击登录页的重置密码按钮。", source="faq.md", title="FAQ", embedder=FakeEmbedder())
    kb.ingest("退款需要联系客服并提供订单号。", source="refund.md", title="退款", embedder=FakeEmbedder())

    hits = kb.search("怎么重置密码", k=1, embedder=FakeEmbedder())
    assert hits, "should retrieve a result"
    assert hits[0]["source"] == "faq.md"
    assert hits[0]["score"] > 0.9


def test_ingest_overwrites_same_source(kb):
    kb.ingest("旧内容。", source="a.md", embedder=FakeEmbedder())
    kb.ingest("新内容。", source="a.md", embedder=FakeEmbedder())
    assert len(kb.list_documents()) == 1
    assert kb.stats() == {"documents": 1, "chunks": 1}


def test_delete_document(kb):
    kb.ingest("一些内容。", source="x.md", embedder=FakeEmbedder())
    assert kb.delete_document("x.md") is True
    assert kb.stats()["documents"] == 0


def test_search_empty_db_and_empty_query(kb):
    assert kb.search("任何问题", embedder=FakeEmbedder()) == []
    kb.ingest("密码相关。", source="a.md", embedder=FakeEmbedder())
    assert kb.search("   ", embedder=FakeEmbedder()) == []


def test_dim_mismatch_filtered(kb):
    """After switching embedding models the dimension changes; old vectors must be filtered out
    (otherwise the matrix multiply crashes)."""
    kb.ingest("密码相关内容。", source="a.md", embedder=FakeEmbedder())

    class Other(FakeEmbedder):
        dim = 8

        def embed(self, texts):
            return np.ones((len(texts), 8), dtype="float32") / np.sqrt(8)

    assert kb.search("密码", embedder=Other()) == []


# ---------------------------------------------------------------- tool layer
class _P:
    def __init__(self, arguments=None):
        self.arguments = arguments or {}
        self.result = None
        self.properties = None

    async def result_callback(self, result, properties=None):
        self.result = result
        self.properties = properties


def _run(handler, arguments=None):
    p = _P(arguments)
    asyncio.run(handler(p))
    return p


def test_search_knowledge_tool_hit(monkeypatch):
    import knowledge
    import tools

    monkeypatch.setattr(
        knowledge,
        "search",
        lambda q, k=3: [
            {"source": "faq.md", "title": "FAQ", "seq": 0, "score": 0.91, "text": "重置密码请点击忘记密码"}
        ],
    )
    p = _run(tools.search_knowledge, {"query": "怎么重置密码"})
    assert p.result["found"] is True
    assert "重置密码" in p.result["spoken"]
    assert p.properties.run_llm is True


def test_search_knowledge_tool_empty_query():
    import tools

    p = _run(tools.search_knowledge, {"query": "   "})
    assert p.result["found"] is False


def test_search_knowledge_registered():
    import tools

    names = [t.name for t in tools.build_tools().standard_tools]
    assert "search_knowledge" in names
