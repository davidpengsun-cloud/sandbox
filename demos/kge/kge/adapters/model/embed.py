"""Embeddings for chunks. The gate runs before this module is ever called.

The default is a hashing embedding: deterministic, dependency-free, and good
enough to show filter-then-score behaving. It is not a semantic model and the
README says so. Set EMBEDDING_PROVIDER=local to use all-MiniLM-L6-v2 through
sentence-transformers; if that package is missing the embedder fails loudly
rather than falling back. Its 384 dimensions are reduced to the graph's DIM by
a fixed, seeded random projection, which preserves cosine similarity
approximately (Johnson-Lindenstrauss), not by truncation. Whichever produced a
vector is recorded on the chunk as embedding_model, and retrieval only scores
chunks embedded by the model it is querying with, so two spaces are never
compared. EMBEDDING_PROVIDER=vertex calls Vertex AI's gemini-embedding-001
unless VERTEX_EMBEDDING_MODEL or EMBEDDING_MODEL names another (text-embedding-004,
the default before, is retired: Vertex AI answers 404 for it).

The graph store's chunk vectors are FLOAT[DIM], so every embedder whose vectors
it stores is DIM wide. Where retrieval is BigQuery's (kge.adapters.retrieval
backend()), the vectors go to BigQuery's chunks table and never to the graph
store, so the Vertex AI embedder is EMBEDDING_DIM wide there, 768 unless set:
the same tokens buy a wider separation than at 128. The hash and local
embedders stay DIM wide on either backend. EMBEDDING_DIM other than DIM is
refused where retrieval is the graph store's: its vectors would not fit.
"""
from __future__ import annotations

import hashlib
import threading
from typing import Any

import numpy as np

from kge.pipeline.envfile import load_env
from kge.adapters.model.errors import post_json, vertex_url
from kge.pipeline.settings import settings

DIM = 128
PROJECTION_SEED = 20240701
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
DEFAULT_REGION = "global"
DEFAULT_VERTEX_MODEL = "gemini-embedding-001"
BIGQUERY_DIM = 768


class HashEmbedder:
    name = "hash-v1"

    def __init__(self, dim: int = DIM):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        v = np.zeros(self.dim)
        for tok in text.lower().split():
            v[int(hashlib.blake2s(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = np.linalg.norm(v) or 1.0
        return (v / n).astype(float).tolist()


class LocalEmbedder:
    model_name = "all-MiniLM-L6-v2"

    def __init__(self, dim: int = DIM):
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise RuntimeError("EMBEDDING_PROVIDER=local needs the sentence-transformers package "
                               "(pip install sentence-transformers); set EMBEDDING_PROVIDER=hash to use the hashing embedder") from exc
        self.m = SentenceTransformer(self.model_name)
        self.dim = dim
        n = int(self.m.get_sentence_embedding_dimension())
        if n == self.dim:
            self.P = None
            self.name = self.model_name
        else:
            self.P = np.random.default_rng(PROJECTION_SEED).standard_normal((n, self.dim)) / np.sqrt(self.dim)
            self.name = f"{self.model_name}+jl{self.dim}"

    def embed(self, text: str) -> list[float]:
        v = np.asarray(self.m.encode(text, normalize_embeddings=True), dtype=float)
        if self.P is not None:
            v = v @ self.P
        n = np.linalg.norm(v) or 1.0
        return (v / n).tolist()


class VertexEmbedder:
    def __init__(self, project: str | None = None, region: str | None = None, dim: int | None = None, model: str | None = None):
        """dim: the vector's width; None reads it from the settings (vertex_dim)."""
        self.dim = vertex_dim() if dim is None else dim
        self.model = (model or settings().VERTEX_EMBEDDING_MODEL or settings().EMBEDDING_MODEL or "").strip() or DEFAULT_VERTEX_MODEL
        self.name = self.model
        self.region = (region or settings().VERTEX_REGION or "").strip() or DEFAULT_REGION
        self.project = (project or settings().VERTEX_PROJECT or "").strip() or None
        self._account: tuple | None = None
        self._lock = threading.Lock()

    def account(self) -> tuple[Any, str]:
        with self._lock:
            if self._account is None:
                import google.auth
                credentials, project = google.auth.default(scopes=[CLOUD_PLATFORM])
                self._account = (credentials, self.project or project)
        credentials, project = self._account
        if not project:
            raise ValueError("no Google Cloud project: set VERTEX_PROJECT, or run where the credentials name one")
        return credentials, project

    def token(self) -> tuple[str, str]:
        credentials, project = self.account()
        with self._lock:
            if not credentials.valid:
                from google.auth.transport.requests import Request
                credentials.refresh(Request())
            return credentials.token, project

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        """ExternalCallError, "Vertex AI error (HTTP <code>): <message>", if the call fails."""
        return post_json(url, headers, body, service="Vertex AI")

    def embed(self, text: str) -> list[float]:
        token, project = self.token()
        url = f"{vertex_url(self.region)}/projects/{project}/locations/{self.region}/publishers/google/models/{self.model}:predict"
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        body = {
            "instances": [{"content": text}],
            "parameters": {"outputDimensionality": self.dim},
        }
        data = self._post(url, headers, body)
        values = data["predictions"][0]["embeddings"]["values"]
        v = np.asarray(values, dtype=float)
        n = np.linalg.norm(v) or 1.0
        return (v / n).tolist()


def vectors_in_bigquery() -> bool:
    """Whether chunk vectors live in BigQuery's chunks table rather than the graph store: retrieval's backend is bigquery
    (RETRIEVAL_BACKEND, else WAREHOUSE_BACKEND, as kge.adapters.retrieval reads it)."""
    from kge.adapters.retrieval import backend
    return backend() == "bigquery"


def vertex_dim() -> int:
    """The Vertex AI embedder's width: EMBEDDING_DIM, else 768 where the vectors live in BigQuery and DIM where they
    live in the graph store, which refuses any other width. A value that is not a whole number above 0 is refused."""
    raw = (settings().EMBEDDING_DIM or "").strip()
    bigquery = vectors_in_bigquery()
    if not raw:
        return BIGQUERY_DIM if bigquery else DIM
    try:
        n = int(raw)
    except ValueError:
        n = 0
    if n < 1:
        raise ValueError(f"EMBEDDING_DIM must be a whole number above 0, not {raw!r}")
    if not bigquery and n != DIM:
        raise ValueError(f"EMBEDDING_DIM={n} needs RETRIEVAL_BACKEND=bigquery: the graph store's chunk vectors are "
                         f"FLOAT[{DIM}]; leave it unset or set it to {DIM}")
    return n


def make_embedder(dim: int | None = None, model: str | None = None):
    """The embedder EMBEDDING_PROVIDER names, dim wide where asked; else DIM, and for Vertex AI vertex_dim()."""
    load_env()
    provider = (settings().EMBEDDING_PROVIDER or "hash").strip().lower() or "hash"
    if provider == "hash":
        return HashEmbedder(dim=DIM if dim is None else dim)
    if provider == "local":
        return LocalEmbedder(dim=DIM if dim is None else dim)
    if provider == "vertex":
        return VertexEmbedder(dim=dim, model=model)
    raise ValueError(f"EMBEDDING_PROVIDER must be hash, local or vertex, not {provider!r}")
