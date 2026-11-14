"""Environment-backed service settings, read afresh for each call, and ROOT, the demo's own folder: the one place code
finds the files beside the package (the meta-model, the page, the standards, clients/, deploy/), whichever folder it
runs from."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ENVIRONMENT_NAMES = (
    "ACCESS_ADMINS", "ADMINS", "ARM",
    "GRANT", "REVOKE", "LIST", "AUDIT", "MIGRATE", "EMAILS", "BY",
    "ASKER", "BASELINE", "CLOUD_RUN_TASK_ATTEMPT", "CONFIRM", "PEOPLE", "PREVIEW", "PREVIOUS", "PRINCIPAL", "ROTATE",
    "QUESTION_STATE_BACKEND", "QUESTION_RUN_TIMEOUT", "QUESTION_POLL_SECONDS", "QUESTION_START_WAIT",
    "API_URL", "APP_STATE_BACKEND", "BIGQUERY_DATASET_PREFIX", "BIGQUERY_JOB_TIMEOUT", "BIGQUERY_LOAD_INLINE_MAX",
    "BIGQUERY_MAX_BYTES_BILLED", "BIGQUERY_PROJECT", "CLIENT", "CLIENT_NAME",
    "CLOUD_RUN_API", "CONTEXT_APP_NAME", "CONTEXT_URL", "DATA_EXPORT_ROW_CAP", "DEFAULT_RUNTIMES", "DEMO_MODE", "DOCUMENT_READER", "EMBEDDING_DIM", "EMBEDDING_MODEL", "EMBEDDING_PROVIDER", "EVAL_CONCURRENCY", "EXAMPLE",
    "FROM", "GRAPH_DATASET", "GRAPH_STORE", "HASH", "KGE_LOGO", "KGE_VERSION", "K_SERVICE", "LADYBUG_MAX_DB_MB", "LANDING_URL", "MOCK_MODE",
    "MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_HIGH_MAX_TOKENS", "MODEL_LOW_MAX_TOKENS", "MODEL_MAX_TOKENS",
    "MODEL_MEDIUM_MAX_TOKENS", "MODEL_PROVIDER", "MODEL_REQUEST_TIMEOUT", "MODEL_SEED", "MODEL_TIMEOUT", "MODEL_TOTAL_TIMEOUT", "NAME", "ONBOARD_MODEL_RETRIES", "PAST_QUESTIONS_RETENTION_DAYS", "PROFILE", "PROFILES",
    "PORTAL_SYNC_TOKEN", "PORTAL_URL", "PRODUCT_NAME", "RETRIEVAL_BACKEND", "RUNTIMES", "RUN_JOB", "RUN_REGION", "SEARCH_PASSAGE_MIN_SIMILARITY", "SERVE_HASH", "SESSION_STATE_BACKEND", "SESSION_SECRET", "SIGNIN", "SIGNIN_PASSWORDS",
    "STORAGE_EMULATOR_HOST", "STORE_URL", "SUGGESTED_MENTION_MIN_SIMILARITY", "SUGGESTED_MENTION_CAP", "SUITE", "TAGGER_NAME", "TAGGER_PROVIDER", "TMP_URL", "TRUSTED_CALLER_AUDIENCE",
    "TRUSTED_CALLER_EMAIL", "VERTEX_EMBEDDING_MODEL", "VERTEX_EMBEDDING_REGION",
    "VERTEX_PROJECT", "VERTEX_REGION", "WAREHOUSE_BACKEND",
)


@dataclass(frozen=True)
class Settings:
    ACCESS_ADMINS: str | None = None
    ADMINS: str | None = None
    ARM: str | None = None
    GRANT: str | None = None
    REVOKE: str | None = None
    LIST: str | None = None
    AUDIT: str | None = None
    MIGRATE: str | None = None
    EMAILS: str | None = None
    BY: str | None = None
    ASKER: str | None = None
    BASELINE: str | None = None
    CLOUD_RUN_TASK_ATTEMPT: str | None = None
    CONFIRM: str | None = None
    PEOPLE: str | None = None
    PREVIEW: str | None = None
    PREVIOUS: str | None = None
    PRINCIPAL: str | None = None
    ROTATE: str | None = None
    QUESTION_STATE_BACKEND: str | None = None
    QUESTION_RUN_TIMEOUT: str | None = None
    QUESTION_POLL_SECONDS: str | None = None
    QUESTION_START_WAIT: str | None = None
    API_URL: str | None = None
    APP_STATE_BACKEND: str | None = None
    BIGQUERY_DATASET_PREFIX: str | None = None
    BIGQUERY_JOB_TIMEOUT: str | None = None
    BIGQUERY_LOAD_INLINE_MAX: str | None = None
    BIGQUERY_MAX_BYTES_BILLED: str | None = None
    BIGQUERY_PROJECT: str | None = None
    CLIENT: str | None = None
    CLIENT_NAME: str | None = None
    CLOUD_RUN_API: str | None = None
    CONTEXT_APP_NAME: str | None = None
    CONTEXT_URL: str | None = None
    DATA_EXPORT_ROW_CAP: str | None = None
    DEFAULT_RUNTIMES: str | None = None
    DOCUMENT_READER: str | None = None
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
    PAST_QUESTIONS_RETENTION_DAYS: str | None = None
    PORTAL_SYNC_TOKEN: str | None = None
    PORTAL_URL: str | None = None
    PRODUCT_NAME: str | None = None
    PROFILE: str | None = None
    PROFILES: str | None = None
    RETRIEVAL_BACKEND: str | None = None
    RUNTIMES: str | None = None
    RUN_JOB: str | None = None
    RUN_REGION: str | None = None
    SEARCH_PASSAGE_MIN_SIMILARITY: str | None = None
    SERVE_HASH: str | None = None
    SESSION_STATE_BACKEND: str | None = None
    SESSION_SECRET: str | None = None
    SIGNIN: str | None = None
    SIGNIN_PASSWORDS: str | None = None
    STORAGE_EMULATOR_HOST: str | None = None
    STORE_URL: str | None = None
    SUGGESTED_MENTION_MIN_SIMILARITY: str | None = None
    SUGGESTED_MENTION_CAP: str | None = None
    SUITE: str | None = None
    TAGGER_NAME: str | None = None
    TAGGER_PROVIDER: str | None = None
    TMP_URL: str | None = None
    TRUSTED_CALLER_AUDIENCE: str | None = None
    TRUSTED_CALLER_EMAIL: str | None = None
    VERTEX_EMBEDDING_MODEL: str | None = None
    VERTEX_EMBEDDING_REGION: str | None = None
    VERTEX_PROJECT: str | None = None
    VERTEX_REGION: str | None = None
    WAREHOUSE_BACKEND: str | None = None


def settings() -> Settings:
    """Return the current process environment without caching test mutations."""
    return Settings(**{name: os.environ.get(name) for name in ENVIRONMENT_NAMES})


DEFAULT_PRODUCT_NAME = "ALTA"


def product_name() -> str:
    """Name KGE's pages from the portal's operator values, independently of the profile or pack title."""
    current = settings()
    return ((current.CLIENT_NAME or "").strip() or "Client") + " " + ((current.PRODUCT_NAME or "").strip() or DEFAULT_PRODUCT_NAME)


def context_app_name() -> str:
    """The Context app's own display name, independent of Discovery's PRODUCT_NAME."""
    return (settings().CONTEXT_APP_NAME or "").strip() or "Context"


def run_job() -> str:
    """The explicitly named job, or the existing service-name default."""
    current = settings()
    return (current.RUN_JOB or "").strip() or (current.K_SERVICE or "").strip()


def check_service_auth_settings() -> None:
    """Refuse invalid service settings before the HTTP service starts."""
    current = settings()
    if (current.K_SERVICE and (current.SIGNIN or "").strip().lower() == "passwords"
            and not (current.SESSION_SECRET or "").strip()):
        raise ValueError("SESSION_SECRET is required on Cloud Run with SIGNIN=passwords")
    token = settings().PORTAL_SYNC_TOKEN
    if token and len(token) < 32:
        raise ValueError("PORTAL_SYNC_TOKEN must be at least 32 characters")
    from kge.pipeline.questionrun import check_settings
    check_settings()


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


def bigquery_max_bytes_billed(default: int | None = None) -> int | None:
    """Maximum bytes billed per BigQuery query from BIGQUERY_MAX_BYTES_BILLED, or default when unset."""
    raw = (settings().BIGQUERY_MAX_BYTES_BILLED or "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"BIGQUERY_MAX_BYTES_BILLED={raw!r}: a whole number of bytes above 0")
    return int(raw)


def default_runtimes() -> list[str] | None:
    """The deployment's ordered default runtime id list from DEFAULT_RUNTIMES (comma-separated), or None when unset."""
    raw = (settings().DEFAULT_RUNTIMES or "").strip()
    if not raw:
        return None
    items = [part.strip() for part in raw.split(",") if part.strip()]
    return items or None


def suggested_mention_settings(default: float) -> tuple[float, int]:
    """The owner-calibrated similarity floor and maximum suggestions per onboarding."""
    current = settings()
    raw = current.SUGGESTED_MENTION_MIN_SIMILARITY
    try:
        threshold = float(raw) if raw and raw.strip() else default
    except ValueError:
        threshold = -1
    if not 0 <= threshold <= 1:
        raise ValueError("SUGGESTED_MENTION_MIN_SIMILARITY must be a number between 0 and 1")
    raw = current.SUGGESTED_MENTION_CAP
    try:
        cap = int(raw) if raw and raw.strip() else 20
    except ValueError:
        cap = -1
    if cap < 0:
        raise ValueError("SUGGESTED_MENTION_CAP must be a whole number at least 0")
    return threshold, cap


def past_questions_retention_days() -> int:
    """Retention window in days for Context Ask's per-person question and conversation history (default 30)."""
    raw = settings().PAST_QUESTIONS_RETENTION_DAYS
    try:
        days = int(raw) if raw and raw.strip() else 30
    except ValueError:
        days = -1
    if days < 0:
        raise ValueError(f"PAST_QUESTIONS_RETENTION_DAYS must be a whole number at least 0, not {raw!r}")
    return days
