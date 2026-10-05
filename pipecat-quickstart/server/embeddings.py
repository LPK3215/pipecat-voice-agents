"""Text embeddings -- the base layer of RAG, with an interface first and swappable backends.

Why a separate module:
    Embeddings are a prerequisite for "knowledge base / semantic memory", but they
    **belong to no single LLM provider**. The provider used here has **no embeddings
    endpoint** (measured: /v1/embeddings returns 404), so the default is a local model --
    consistent with the STT/TTS choice: local when possible, cheaper and not vendor-locked.

Two implementations, switched by ``EMBEDDING_PROVIDER`` (default local):
    local   local transformers + torch running ``BAAI/bge-small-zh-v1.5`` (512-dim, Chinese, no key)
    api     any OpenAI-compatible ``/v1/embeddings`` (needs EMBEDDING_BASE_URL / EMBEDDING_API_KEY)

Design constraint: expose only ``embed(texts) -> ndarray[N, dim]`` (L2-normalized).
Upper layers (knowledge.py) depend only on this contract, so swapping the backend
requires no changes there or in the tools.
"""

from __future__ import annotations

import os
from typing import Protocol

import numpy as np

DEFAULT_LOCAL_MODEL = "BAAI/bge-small-zh-v1.5"
_MAX_LEN = 512


class Embedder(Protocol):
    """Embedding backend contract: takes a batch of texts, returns an L2-normalized matrix."""

    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - protocol
        ...


class LocalEmbedder:
    """Local embeddings: transformers + torch running a sentence-embedding model
    (default bge-small-zh-v1.5).

    Lazy loading: the model is loaded only on the first ``embed()`` call, so unused
    scenarios are not slowed down.
    """

    def __init__(self, model_name: str | None = None) -> None:
        self._name = model_name or os.getenv("EMBEDDING_MODEL", DEFAULT_LOCAL_MODEL)
        self._tok = None
        self._mdl = None
        self.dim = 0  # determined after loading

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
        # Mean pooling + L2 normalization (standard usage for the bge family).
        vec = out.last_hidden_state.mean(dim=1)
        vec = torch.nn.functional.normalize(vec, p=2, dim=1)
        return vec.cpu().numpy().astype("float32")


class OpenAICompatEmbedder:
    """OpenAI-compatible ``/v1/embeddings`` backend (for switching to a cloud embedder later)."""

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
        self.dim = 0  # determined by the first response

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
    """Return the singleton embedder chosen by ``EMBEDDING_PROVIDER`` (default local)."""
    global _EMBEDDER
    if _EMBEDDER is None or force:
        provider = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower()
        if provider in ("api", "cloud", "openai"):
            _EMBEDDER = OpenAICompatEmbedder()
        else:
            _EMBEDDER = LocalEmbedder()
    return _EMBEDDER
