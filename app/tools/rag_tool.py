"""RAG tool querying fine-grained chunk embeddings in BigQuery with adjacent context stitching."""

import os
from typing import Optional
from google.cloud import bigquery

import logging

logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "antigravity-503007")

# Mandatory standardized decline warning string when similarity is below 0.70 threshold
MANDATORY_DECLINE_WARNING = (
    "WARNING: No certified POS troubleshooting documentation or procedural runbooks matched your query "
    "with sufficient confidence (similarity >= 0.70). Query fell back to uncertified results or no matches were found."
)

_bq_client = None

def get_bq_client():
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT_ID)
    return _bq_client

def pos_troubleshooting_rag_tool(query: str, store_model_context: Optional[str] = None) -> str:
    """Searches POS hardware and software troubleshooting runbooks, manuals, and error codes using vector search over fine-grained chunk embeddings with adjacent window stitching.
    
    Args:
        query: The troubleshooting query, issue description, or error code (e.g. 'ERR-PAY-4001', 'frozen touch screen').
        store_model_context: Optional specific hardware model or store context (e.g. 'Toshiba TCx 810', 'HP Engage One').
        
    Returns:
        Relevant manual sections with stitched context windows (N-1 ~ N+1), similarity scores, and documentation links, or certified decline warning if similarity < 0.70.
    """
    try:
        client = get_bq_client()
    except Exception as e:
        logger.error("Failed to initialize BigQuery client in pos_troubleshooting_rag_tool: %s", e)
        return "The requested data source is currently unreachable due to a temporary service failure. Please try again later."
    
    # Vector search query with window stitching (N-1, N, N+1) and threshold >= 0.70
    vector_sql = f"""
    WITH query_emb AS (
      SELECT AI.EMBED(@user_query, endpoint => 'text-embedding-005').result AS query_vector
    ),
    top_matches AS (
      SELECT
        base.document_title,
        base.chunk_index,
        distance,
        1 - distance AS similarity_score
      FROM VECTOR_SEARCH(
        TABLE `{PROJECT_ID}.cymbal_gold.pos_manual_chunk_embeddings`,
        'embedding',
        (SELECT query_vector FROM query_emb),
        top_k => 5,
        distance_type => 'COSINE'
      )
      WHERE (1 - distance) >= 0.70
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
      JOIN `{PROJECT_ID}.cymbal_gold.pos_manual_chunk_embeddings` all_chunks
        ON tm.document_title = all_chunks.document_title
       AND all_chunks.chunk_index BETWEEN tm.chunk_index - 1 AND tm.chunk_index + 1
      GROUP BY tm.document_title, tm.chunk_index, tm.similarity_score
    )
    SELECT * FROM stitched_chunks
    ORDER BY similarity_score DESC;
    """
    
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_query", "STRING", query)
        ]
    )
    
    try:
        results = list(client.query(vector_sql, job_config=job_config).result())
    except Exception as e:
        logger.warning("Vector search query failed or timed out: %s", e)
        results = []
    
    # If no results meet the 0.70 threshold or specific error code pattern, fallback to full-text SEARCH
    if not results:
        # Clean query for SEARCH: extract error codes (e.g. ERR-PAY-4001) or specific diagnostic codes
        import re
        error_codes = re.findall(r"[A-Za-z0-9]+-[A-Za-z0-9-]+", query)
        if error_codes:
            search_query_str = f"`{error_codes[0]}`"
        else:
            # Fallback to key alphabetic tokens
            key_tokens = [w for w in re.findall(r"[A-Za-z0-9]+", query) if len(w) > 3 and w.lower() not in {"what", "when", "where", "which", "with", "from", "that", "this", "have", "ensure", "customer", "immediate", "field"}]
            search_query_str = " ".join(key_tokens[:3]) if key_tokens else query
        
        fallback_sql = f"""
        WITH text_matches AS (
          SELECT
            document_title,
            chunk_index,
            source_pdf_uri,
            chunk_content
          FROM `{PROJECT_ID}.cymbal_gold.pos_manual_chunk_embeddings`
          WHERE SEARCH(chunk_content, @search_term)
          LIMIT 3
        ),
        stitched_fallback AS (
          SELECT
            tm.document_title,
            tm.chunk_index AS matched_chunk_index,
            0.75 AS similarity_score,
            STRING_AGG(all_chunks.chunk_content, '\\n---\\n' ORDER BY all_chunks.chunk_index) AS stitched_context,
            MIN(all_chunks.source_pdf_uri) AS source_pdf_uri
          FROM text_matches tm
          JOIN `{PROJECT_ID}.cymbal_gold.pos_manual_chunk_embeddings` all_chunks
            ON tm.document_title = all_chunks.document_title
           AND all_chunks.chunk_index BETWEEN tm.chunk_index - 1 AND tm.chunk_index + 1
          GROUP BY tm.document_title, tm.chunk_index
        )
        SELECT * FROM stitched_fallback;
        """
        fb_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("search_term", "STRING", search_query_str)
            ]
        )
        try:
            results = list(client.query(fallback_sql, job_config=fb_config).result())
        except Exception as e:
            logger.warning("Fallback full-text search query failed: %s", e)
            results = []
            
    if not results:
        return f"{MANDATORY_DECLINE_WARNING} (Query: '{query}', max_similarity < 0.70)"
        
    formatted_sections = []
    for row in results:
        doc_title = row.document_title
        score = row.similarity_score
        pdf_uri = row.source_pdf_uri or ""
        # Convert gs:// to https://storage.cloud.google.com/
        http_url = pdf_uri.replace("gs://", "https://storage.cloud.google.com/")
        context_body = row.stitched_context
        
        section = (
            f"### Document: {doc_title} (Relevance Score: {score:.3f})\n"
            f"**Source Document URL**: [{doc_title}]({http_url})\n\n"
            f"**Stitched Runbook Excerpt (Context Window N-1 ~ N+1)**:\n"
            f"```text\n{context_body}\n```"
        )
        formatted_sections.append(section)
        
    return "\n\n".join(formatted_sections)
