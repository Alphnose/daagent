"""Centralized runtime configuration resolver for the Cymbal Operations Agent.

All environment-specific values (GCP project, region, dataset/table names, Data Agent
resource path, Bigtable instance, MCP endpoint) are resolved lazily from environment
variables or Application Default Credentials (ADC). No GCP resource identifier is ever
hardcoded in application logic, which keeps the codebase portable across dev / staging /
prod projects and CI runners.

Resolution order for the project id:
  1. ``PROJECT_ID``
  2. ``GOOGLE_CLOUD_PROJECT``
  3. ``GCLOUD_PROJECT`` / ``GCP_PROJECT``
  4. Project bound to Application Default Credentials (``google.auth.default()``)

If none resolve, :class:`ConfigurationError` is raised with an actionable message rather
than silently falling back to a foreign project.
"""

import functools
import logging
import os
from typing import Optional

try:  # python-dotenv is optional at runtime (e.g. inside containers using real env vars)
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is a convenience, never a requirement
    pass

logger = logging.getLogger(__name__)

# Non environment-specific defaults (safe to keep in code).
DEFAULT_REGION = "us-central1"
DEFAULT_DATA_AGENT_LOCATION = "global"
DEFAULT_DATA_AGENT_ID = "cymbal-retail-analytics-data-agent"
DEFAULT_GOLD_DATASET = "cymbal_gold"
DEFAULT_CHUNK_EMBEDDINGS_TABLE = "pos_manual_chunk_embeddings"
DEFAULT_BIGTABLE_INSTANCE_ID = "operations-db"
DEFAULT_BIGTABLE_TABLE_ID = "cashier_realtime_alerts"
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_EMBEDDING_ENDPOINT = "text-embedding-005"
DEFAULT_RAG_SIMILARITY_THRESHOLD = 0.70
DEFAULT_BQ_TELEMETRY_DATASET = "agent_telemetry"
DEFAULT_BQ_TELEMETRY_TABLE = "events"

_PROJECT_ENV_VARS = (
    "PROJECT_ID",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "GCP_PROJECT",
)


class ConfigurationError(RuntimeError):
    """Raised when a required environment-specific setting cannot be resolved."""


def _env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


@functools.lru_cache(maxsize=1)
def get_project_id() -> str:
    """Resolves the active GCP project id from the environment or ADC."""
    for var in _PROJECT_ENV_VARS:
        value = _env(var)
        if value:
            logger.debug("Resolved project id from %s.", var)
            return value

    try:
        import google.auth

        _, adc_project = google.auth.default()
        if adc_project:
            logger.info("Resolved project id from Application Default Credentials.")
            return adc_project
    except Exception as exc:  # pragma: no cover - depends on local credentials state
        logger.debug("ADC project lookup unavailable: %s", exc)

    raise ConfigurationError(
        "Unable to resolve the GCP project id. Set PROJECT_ID (or GOOGLE_CLOUD_PROJECT) "
        "in your environment / .env file, or run `gcloud auth application-default login "
        "--project <PROJECT_ID>`."
    )


@functools.lru_cache(maxsize=1)
def get_region() -> str:
    """Resolves the deployment region (defaults to ``us-central1``)."""
    return _env("REGION") or _env("GOOGLE_CLOUD_REGION") or DEFAULT_REGION


@functools.lru_cache(maxsize=1)
def get_data_agent_name() -> str:
    """Resolves the fully-qualified BigQuery Conversational Data Agent resource path.

    Prefers an explicit ``DATA_AGENT_NAME``; otherwise composes the path from the
    resolved project id, ``DATA_AGENT_LOCATION`` and ``DATA_AGENT_ID``.
    """
    explicit = _env("DATA_AGENT_NAME")
    if explicit:
        return explicit

    location = _env("DATA_AGENT_LOCATION") or DEFAULT_DATA_AGENT_LOCATION
    agent_id = _env("DATA_AGENT_ID") or DEFAULT_DATA_AGENT_ID
    return f"projects/{get_project_id()}/locations/{location}/dataAgents/{agent_id}"


@functools.lru_cache(maxsize=1)
def get_gold_dataset() -> str:
    return _env("BQ_GOLD_DATASET") or DEFAULT_GOLD_DATASET


@functools.lru_cache(maxsize=1)
def get_chunk_embeddings_table() -> str:
    """Fully-qualified BigQuery table holding the fine-grained chunk embeddings."""
    explicit = _env("BQ_CHUNK_EMBEDDINGS_TABLE")
    if explicit:
        return explicit
    table = _env("BQ_CHUNK_EMBEDDINGS_TABLE_ID") or DEFAULT_CHUNK_EMBEDDINGS_TABLE
    return f"{get_project_id()}.{get_gold_dataset()}.{table}"


@functools.lru_cache(maxsize=1)
def get_bigtable_instance_id() -> str:
    return _env("BIGTABLE_INSTANCE_ID") or DEFAULT_BIGTABLE_INSTANCE_ID


@functools.lru_cache(maxsize=1)
def get_bigtable_table_id() -> str:
    return _env("BIGTABLE_TABLE_ID") or DEFAULT_BIGTABLE_TABLE_ID


@functools.lru_cache(maxsize=1)
def get_bigtable_mcp_url() -> Optional[str]:
    """Base URL of the deployed Database Toolbox (MCP) Cloud Run microservice.

    Returns ``None`` when unset so the toolset can degrade gracefully instead of
    attempting to reach another tenant's endpoint.
    """
    url = _env("BIGTABLE_MCP_URL")
    return url.rstrip("/") if url else None


@functools.lru_cache(maxsize=1)
def get_gemini_model() -> str:
    return _env("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


@functools.lru_cache(maxsize=1)
def get_embedding_endpoint() -> str:
    return _env("EMBEDDING_ENDPOINT") or DEFAULT_EMBEDDING_ENDPOINT


@functools.lru_cache(maxsize=1)
def get_rag_similarity_threshold() -> float:
    raw = _env("RAG_SIMILARITY_THRESHOLD")
    if not raw:
        return DEFAULT_RAG_SIMILARITY_THRESHOLD
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "Invalid RAG_SIMILARITY_THRESHOLD=%r, falling back to %.2f.",
            raw,
            DEFAULT_RAG_SIMILARITY_THRESHOLD,
        )
        return DEFAULT_RAG_SIMILARITY_THRESHOLD


@functools.lru_cache(maxsize=1)
def get_bq_telemetry_dataset() -> str:
    """BigQuery dataset receiving the ADK agent analytics event stream."""
    return _env("BQ_TELEMETRY_DATASET") or DEFAULT_BQ_TELEMETRY_DATASET


@functools.lru_cache(maxsize=1)
def get_bq_telemetry_table() -> str:
    """BigQuery table receiving the ADK agent analytics event stream."""
    return _env("BQ_TELEMETRY_TABLE") or DEFAULT_BQ_TELEMETRY_TABLE


@functools.lru_cache(maxsize=1)
def get_bq_telemetry_location() -> str:
    """BigQuery location of the telemetry dataset.

    Defaults to :func:`get_region` because the dataset is provisioned alongside the
    agent runtime; override with ``BQ_TELEMETRY_LOCATION`` for multi-region datasets.
    """
    return _env("BQ_TELEMETRY_LOCATION") or get_region()


@functools.lru_cache(maxsize=1)
def is_telemetry_enabled() -> bool:
    """Whether the BigQuery agent analytics plugin should be attached.

    Enabled by default. Set ``ENABLE_BQ_TELEMETRY=false`` to run the agent without
    streaming telemetry (useful for offline unit tests or air-gapped environments).
    """
    raw = _env("ENABLE_BQ_TELEMETRY")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def reset_cache() -> None:
    """Clears memoized values (used by tests that patch the environment)."""
    for resolver in (
        get_project_id,
        get_region,
        get_data_agent_name,
        get_gold_dataset,
        get_chunk_embeddings_table,
        get_bigtable_instance_id,
        get_bigtable_table_id,
        get_bigtable_mcp_url,
        get_gemini_model,
        get_embedding_endpoint,
        get_rag_similarity_threshold,
        get_bq_telemetry_dataset,
        get_bq_telemetry_table,
        get_bq_telemetry_location,
        is_telemetry_enabled,
    ):
        resolver.cache_clear()
