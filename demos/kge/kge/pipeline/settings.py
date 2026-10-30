"""Environment-backed service settings, read afresh for each call, and ROOT, the demo's own folder: the one place code
finds the files beside the package (the meta-model, the page, the standards, clients/, deploy/), whichever folder it
runs from."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ENVIRONMENT_NAMES = (
    "API_URL", "APP_STATE_BACKEND", "BIGQUERY_DATASET_PREFIX", "BIGQUERY_LOAD_INLINE_MAX", "BIGQUERY_PROJECT", "CLIENT",
    "CLOUD_RUN_API", "CONTEXT_URL", "DATA_EXPORT_ROW_CAP", "DEMO_MODE", "EMBEDDING_DIM", "EMBEDDING_MODEL", "EMBEDDING_PROVIDER", "EVAL_CONCURRENCY", "EXAMPLE",
    "FROM", "GRAPH_DATASET", "GRAPH_STORE", "HASH", "KGE_LOGO", "KGE_VERSION", "K_SERVICE", "LADYBUG_MAX_DB_MB", "LANDING_URL", "MOCK_MODE",
    "MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_HIGH_MAX_TOKENS", "MODEL_LOW_MAX_TOKENS", "MODEL_MAX_TOKENS",
    "MODEL_MEDIUM_MAX_TOKENS", "MODEL_PROVIDER", "MODEL_REQUEST_TIMEOUT", "MODEL_SEED", "MODEL_TIMEOUT", "MODEL_TOTAL_TIMEOUT", "NAME", "ONBOARD_MODEL_RETRIES", "PROFILE", "PROFILES",
    "PORTAL_SYNC_TOKEN", "PORTAL_URL", "RETRIEVAL_BACKEND", "RUN_REGION", "SEARCH_PASSAGE_MIN_SIMILARITY", "SERVE_HASH", "SESSION_SECRET", "SIGNIN", "SIGNIN_PASSWORDS",
    "STORAGE_EMULATOR_HOST", "STORE_URL", "TAGGER_NAME", "TAGGER_PROVIDER", "TMP_URL", "TRUSTED_CALLER_AUDIENCE",
    "TRUSTED_CALLER_EMAIL", "VERTEX_EMBEDDING_MODEL",
    "VERTEX_PROJECT", "VERTEX_REGION", "WAREHOUSE_BACKEND",
)


@dataclass(frozen=True)
class Settings:
    API_URL: str | None = None
    APP_STATE_BACKEND: str | None = None
    BIGQUERY_DATASET_PREFIX: str | None = None
    BIGQUERY_LOAD_INLINE_MAX: str | None = None
    BIGQUERY_PROJECT: str | None = None
    CLIENT: str | None = None
    CLOUD_RUN_API: str | None = None
    CONTEXT_URL: str | None = None
    DATA_EXPORT_ROW_CAP: str | None = None
    DEMO_MODE: str | None = None
    EMBEDDING_DIM: str | None = None
    EMBEDDING_MODEL: str | None = None
    EMBEDDING_PROVIDER: str | None = None
    EVAL_CONCURRENCY: str | None = None
    EXAMPLE: str | None = None
    FROM: str | None = None
    GRAPH_DATASET: str | None = None
    GRAPH_STORE: str | None = None
    HASH: str | None = None
    KGE_LOGO: str | None = None
    KGE_VERSION: str | None = None
    K_SERVICE: str | None = None
    LADYBUG_MAX_DB_MB: str | None = None
    LANDING_URL: str | None = None
    MOCK_MODE: str | None = None
    MODEL_API_KEY: str | None = None
    MODEL_BASE_URL: str | None = None
    MODEL_HIGH_MAX_TOKENS: str | None = None
    MODEL_LOW_MAX_TOKENS: str | None = None
    MODEL_MAX_TOKENS: str | None = None
    MODEL_MEDIUM_MAX_TOKENS: str | None = None
    MODEL_PROVIDER: str | None = None
    MODEL_REQUEST_TIMEOUT: str | None = None
    MODEL_SEED: str | None = None
    MODEL_TIMEOUT: str | None = None
    MODEL_TOTAL_TIMEOUT: str | None = None
    NAME: str | None = None
    ONBOARD_MODEL_RETRIES: str | None = None
    PORTAL_SYNC_TOKEN: str | None = None
    PORTAL_URL: str | None = None
    PROFILE: str | None = None
    PROFILES: str | None = None
    RETRIEVAL_BACKEND: str | None = None
    RUN_REGION: str | None = None
    SEARCH_PASSAGE_MIN_SIMILARITY: str | None = None
    SERVE_HASH: str | None = None
    SESSION_SECRET: str | None = None
    SIGNIN: str | None = None
    SIGNIN_PASSWORDS: str | None = None
    STORAGE_EMULATOR_HOST: str | None = None
    STORE_URL: str | None = None
    TAGGER_NAME: str | None = None
    TAGGER_PROVIDER: str | None = None
    TMP_URL: str | None = None
    TRUSTED_CALLER_AUDIENCE: str | None = None
    TRUSTED_CALLER_EMAIL: str | None = None
    VERTEX_EMBEDDING_MODEL: str | None = None
    VERTEX_PROJECT: str | None = None
    VERTEX_REGION: str | None = None
    WAREHOUSE_BACKEND: str | None = None


def settings() -> Settings:
    """Return the current process environment without caching test mutations."""
    return Settings(**{name: os.environ.get(name) for name in ENVIRONMENT_NAMES})


def check_service_auth_settings() -> None:
    """Refuse a configured portal sync secret too short to authenticate a service."""
    token = settings().PORTAL_SYNC_TOKEN
    if token and len(token) < 32:
        raise ValueError("PORTAL_SYNC_TOKEN must be at least 32 characters")


def data_export_row_cap() -> int:
    """The maximum number of rows one data export returns, 10,000 when unset."""
    raw = (settings().DATA_EXPORT_ROW_CAP or "10000").strip()
    try:
        cap = int(raw)
    except ValueError:
        cap = 0
    if cap < 1:
        raise ValueError(f"DATA_EXPORT_ROW_CAP must be a positive whole number, not {raw!r}")
    return cap


def eval_concurrency() -> int:
    """How many questions the A/B harness's runner asks at once (kge/eval/harness.py: make ab, make margin, and the margin
    check onboard and test-drive run): EVAL_CONCURRENCY, 8 when unset; 1 asks them one at a time, as before."""
    raw = (settings().EVAL_CONCURRENCY or "8").strip()
    try:
        workers = int(raw)
    except ValueError:
        workers = 0
    if workers < 1:
        raise ValueError(f"EVAL_CONCURRENCY must be a positive whole number, not {raw!r}")
    return workers


def ladybug_max_db_size() -> int:
    """Ladybug's maximum database size in bytes: LADYBUG_MAX_DB_MB, 128 MB when unset, and always positive."""
    raw = (settings().LADYBUG_MAX_DB_MB or "128").strip()
    try:
        megabytes = int(raw)
    except ValueError:
        megabytes = 0
    if megabytes < 1:
        raise ValueError(f"LADYBUG_MAX_DB_MB must be a positive whole number, not {raw!r}")
    return megabytes * 1024 * 1024


BIGQUERY_BACKENDS = ("WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE")


def gcp_native() -> bool:
    """Whether this process runs the pilot's cloud path: any of WAREHOUSE_BACKEND, RETRIEVAL_BACKEND, APP_STATE_BACKEND
    or GRAPH_STORE is bigquery. On it, a model the settings configure never falls back to rules when its call fails
    (kge/adapters/model/errors.py no_fallback), the warehouses, retrieval and app state never open a DuckDB file, and
    with GRAPH_STORE=bigquery the graph is BigQuery's, never Ladybug's; the local stack, every one of them unset or
    local, keeps its fallbacks."""
    current = settings()
    return any((getattr(current, name) or "").strip().lower() == "bigquery" for name in BIGQUERY_BACKENDS)


def search_passage_min_similarity() -> float:
    """Minimum raw passage similarity for discovery; zero still excludes zero-score hits."""
    raw = settings().SEARCH_PASSAGE_MIN_SIMILARITY
    try:
        value = float(raw) if raw is not None else .2
    except ValueError:
        value = -1
    if not 0 <= value <= 1:
        raise ValueError("SEARCH_PASSAGE_MIN_SIMILARITY must be a number between 0 and 1")
    return value
