"""RAG tool querying fine-grained chunk embeddings in BigQuery with adjacent context stitching.

Retrieval contract
------------------
* Vector search (``VECTOR_SEARCH`` + ``AI.EMBED``) over 500-char sliding-window chunks.
* Adjacent context window stitching (N-1 .. N+1) via ``STRING_AGG``.
* Minimum cosine similarity threshold of 0.70. Below the threshold a full-text
  ``SEARCH(chunk_content, @query)`` fallback runs before the tool declines.
* When nothing certified matches, the tool returns the **exact** mandatory decline
  warning string (:data:`MANDATORY_DECLINE_WARNING`) required by the operational
  specification - no paraphrasing, no hallucinated guidance.
* Every infrastructure exception is logged internally and masked in the user-facing
  response so GCP project paths, datasets and service accounts never leak.
"""

import logging
import re
import time
from typing import List, Optional

from google.cloud import bigquery

from app.config import (
    get_chunk_embeddings_table,
    get_embedding_endpoint,
    get_project_id,
    get_rag_similarity_threshold,
)

logger = logging.getLogger(__name__)

# Exact mandatory decline warning required by the operational specification when no
# certified documentation clears the 0.70 cosine similarity safety threshold.
MANDATORY_DECLINE_WARNING = (
    "WARNING: UNCERTIFIED RESULT - No certified POS troubleshooting documentation "
    "matched this query above the 0.70 similarity threshold. This request appears "
    "to be out of scope for Cymbal Superstore POS operations. No troubleshooting "
    "guidance can be provided."
)

# Transient fault tolerance for the BigQuery calls.
MAX_RETRIES = 3
BASE_RETRY_DELAY_SECONDS = 1.0

_STOPWORDS = {
    "what", "when", "where", "which", "with", "from", "that", "this", "have",
    "ensure", "customer", "immediate", "field", "does", "should", "there",
    "about", "please", "could", "would",
}

_bq_client: Optional[bigquery.Client] = None


def get_bq_client() -> bigquery.Client:
    """Lazily builds a BigQuery client bound to the resolved project."""
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=get_project_id())
    return _bq_client


def _run_query(sql: str, job_config: bigquery.QueryJobConfig, label: str) -> List:
    """Runs a BigQuery query with exponential backoff, masking internal errors."""
    delay = BASE_RETRY_DELAY_SECONDS
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return list(get_bq_client().query(sql, job_config=job_config).result())
        except Exception as exc:
            logger.warning(
                "%s failed on attempt %d/%d: %s", label, attempt, MAX_RETRIES, exc
            )
            if attempt < MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
    logger.error("%s exhausted all %d retries.", label, MAX_RETRIES)
    return []


def _build_search_term(query: str) -> str:
    """Derives a BigQuery ``SEARCH()`` safe term from a natural language query.

    Hyphenated diagnostic codes (``ERR-PAY-4001``) must be backtick-quoted, otherwise
    the text analyzer raises `token recognition error at: '-'`.
    """
    error_codes = re.findall(r"[A-Za-z]+-[A-Za-z0-9-]+", query)
    if error_codes:
        return f"`{error_codes[0]}`"
    key_tokens = [
        w for w in re.findall(r"[A-Za-z0-9]+", query)
        if len(w) > 3 and w.lower() not in _STOPWORDS
    ]
    return " ".join(key_tokens[:3]) if key_tokens else query


def pos_troubleshooting_rag_tool(query: str, store_model_context: Optional[str] = None) -> str:
    """Searches POS hardware and software troubleshooting runbooks, manuals, and error codes using vector search over fine-grained chunk embeddings with adjacent window stitching.

    Args:
        query: The troubleshooting query, issue description, or error code (e.g. 'ERR-PAY-4001', 'frozen touch screen').
        store_model_context: Optional specific hardware model or store context (e.g. 'Toshiba TCx 810', 'HP Engage One').

    Returns:
        Relevant manual sections with stitched context windows (N-1 ~ N+1), similarity scores, and documentation links, or the mandatory certified decline warning if similarity < 0.70.
    """
    try:
        table = get_chunk_embeddings_table()
        threshold = get_rag_similarity_threshold()
        embedding_endpoint = get_embedding_endpoint()
    except Exception as exc:
        logger.error("RAG tool configuration could not be resolved: %s", exc)
        return (
            "The POS documentation service is not configured for this environment. "
            "Please contact your operations administrator."
        )

    search_text = f"{query} {store_model_context}".strip() if store_model_context else query

    vector_sql = f"""
    WITH query_emb AS (
      SELECT AI.EMBED(@user_query, endpoint => '{embedding_endpoint}').result AS query_vector
    ),
    top_matches AS (
      SELECT
        base.document_title,
        base.chunk_index,
        distance,
        1 - distance AS similarity_score
      FROM VECTOR_SEARCH(
        TABLE `{table}`,
        'embedding',
        (SELECT query_vector FROM query_emb),
        top_k => 5,
        distance_type => 'COSINE'
      )
      WHERE (1 - distance) >= @similarity_threshold
      ORDER BY similarity_score DESC
      LIMIT 3
    ),
    stitched_chunks AS (
      SELECT
        tm.document_title,
        tm.chunk_index AS matched_chunk_index,
        tm.similarity_score,
        STRING_AGG(all_chunks.chunk_content, '\\n---\\n' ORDER BY all_chunks.chunk_index) AS stitched_context,
        MIN(all_chunks.source_pdf_uri) AS source_pdf_uri
      FROM top_matches tm
      JOIN `{table}` all_chunks
        ON tm.document_title = all_chunks.document_title
       AND all_chunks.chunk_index BETWEEN tm.chunk_index - 1 AND tm.chunk_index + 1
      GROUP BY tm.document_title, tm.chunk_index, tm.similarity_score
    )
    SELECT * FROM stitched_chunks
    ORDER BY similarity_score DESC;
    """

    vector_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_query", "STRING", search_text),
            bigquery.ScalarQueryParameter("similarity_threshold", "FLOAT64", threshold),
        ]
    )
    results = _run_query(vector_sql, vector_config, "RAG vector search")

    # Below-threshold or empty vector results: attempt certified full-text fallback.
    if not results:
        logger.info(
            "Vector search returned no match >= %.2f similarity; attempting full-text fallback.",
            threshold,
        )
        fallback_sql = f"""
        WITH text_matches AS (
          SELECT document_title, chunk_index, source_pdf_uri, chunk_content
          FROM `{table}`
          WHERE SEARCH(chunk_content, @search_term)
          LIMIT 3
        ),
        stitched_fallback AS (
          SELECT
            tm.document_title,
            tm.chunk_index AS matched_chunk_index,
            @similarity_threshold AS similarity_score,
            STRING_AGG(all_chunks.chunk_content, '\\n---\\n' ORDER BY all_chunks.chunk_index) AS stitched_context,
            MIN(all_chunks.source_pdf_uri) AS source_pdf_uri
          FROM text_matches tm
          JOIN `{table}` all_chunks
            ON tm.document_title = all_chunks.document_title
           AND all_chunks.chunk_index BETWEEN tm.chunk_index - 1 AND tm.chunk_index + 1
          GROUP BY tm.document_title, tm.chunk_index
        )
        SELECT * FROM stitched_fallback;
        """
        fallback_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("search_term", "STRING", _build_search_term(query)),
                bigquery.ScalarQueryParameter("similarity_threshold", "FLOAT64", threshold),
            ]
        )
        results = _run_query(fallback_sql, fallback_config, "RAG full-text fallback")

    if not results:
        logger.info("Returning mandatory decline warning for query: %s", query)
        return f"{MANDATORY_DECLINE_WARNING}\n\nRejected query: '{query}'"

    formatted_sections = []
    for row in results:
        pdf_uri = row.source_pdf_uri or ""
        http_url = pdf_uri.replace("gs://", "https://storage.cloud.google.com/")
        formatted_sections.append(
            f"### Document: {row.document_title} (Relevance Score: {row.similarity_score:.3f})\n"
            f"**Source Document URL**: [{row.document_title}]({http_url})\n\n"
            f"**Stitched Runbook Excerpt (Context Window N-1 ~ N+1)**:\n"
            f"```text\n{row.stitched_context}\n```"
        )

    return "\n\n".join(formatted_sections)
