"""文本嵌入（embedding）—— RAG 的底座，**接口先行、实现可换**。

为什么单独成模块：
    嵌入是"知识库/语义记忆"的前置能力，但它**不属于任何一家 LLM 服务商**。
    本项目当前用的商汤**没有 embeddings 接口**（实测 /v1/embeddings 返回 404），
    所以默认用本地模型——这也和 STT/TTS 的选择一致：能本地就本地，省钱且不受
    服务商绑定。

两个实现，由 ``EMBEDDING_PROVIDER`` 切换（默认 local）：
    local   本地 transformers + torch 跑 ``BAAI/bge-small-zh-v1.5``（512 维，中文，无需 key）
    api     任何 OpenAI 兼容的 ``/v1/embeddings``（需 EMBEDDING_BASE_URL / EMBEDDING_API_KEY）

设计约束：对外只暴露 ``embed(texts) -> ndarray[N, dim]``（已 L2 归一化），
上层（knowledge.py）只依赖这个协议；换后端时上层与工具都不用动。
"""

from __future__ import annotations

import os
from typing import Protocol

import numpy as np

DEFAULT_LOCAL_MODEL = "BAAI/bge-small-zh-v1.5"
_MAX_LEN = 512


class Embedder(Protocol):
    """嵌入后端协议：输入一批文本，返回 L2 归一化后的向量矩阵。"""

    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - 协议
        ...


class LocalEmbedder:
    """本地嵌入：transformers + torch 跑句向量模型（默认 bge-small-zh-v1.5）。

    懒加载：模型只在首次 ``embed()`` 时载入，避免拖慢不用的场景。
    """

    def __init__(self, model_name: str | None = None) -> None:
        self._name = model_name or os.getenv("EMBEDDING_MODEL", DEFAULT_LOCAL_MODEL)
        self._tok = None
        self._mdl = None
        self.dim = 0  # 载入后确定

    def _ensure(self) -> None:
        if self._mdl is not None:
            return
        from transformers import AutoModel, AutoTokenizer

        self._tok = AutoTokenizer.from_pretrained(self._name)
        self._mdl = AutoModel.from_pretrained(self._name).eval()
        self.dim = int(self._mdl.config.hidden_size)

    def embed(self, texts: list[str]) -> np.ndarray:
        import torch

        if not texts:
            return np.zeros((0, self.dim or 1), dtype="float32")
        self._ensure()
        batch = self._tok(
            list(texts), padding=True, truncation=True, return_tensors="pt", max_length=_MAX_LEN
        )
        with torch.no_grad():
            out = self._mdl(**batch)
        # 均值池化 + L2 归一化（bge 系列的常规用法）
        vec = out.last_hidden_state.mean(dim=1)
        vec = torch.nn.functional.normalize(vec, p=2, dim=1)
        return vec.cpu().numpy().astype("float32")


class OpenAICompatEmbedder:
    """OpenAI 兼容的 ``/v1/embeddings`` 后端（给以后换云端嵌入用）。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            base_url=base_url or os.getenv("EMBEDDING_BASE_URL"),
            api_key=api_key or os.getenv("EMBEDDING_API_KEY"),
        )
        self._model = model or os.getenv("EMBEDDING_MODEL", "bge-m3")
        self.dim = 0  # 由首次返回决定

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim or 1), dtype="float32")
        resp = self._client.embeddings.create(model=self._model, input=list(texts))
        arr = np.array([d.embedding for d in resp.data], dtype="float32")
        self.dim = arr.shape[1]
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


_EMBEDDER: Embedder | None = None


def build_embedder(force: bool = False) -> Embedder:
    """按 ``EMBEDDING_PROVIDER`` 返回单例嵌入器（默认 local）。"""
    global _EMBEDDER
    if _EMBEDDER is None or force:
        provider = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower()
        if provider in ("api", "cloud", "openai"):
            _EMBEDDER = OpenAICompatEmbedder()
        else:
            _EMBEDDER = LocalEmbedder()
    return _EMBEDDER
