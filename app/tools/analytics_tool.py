"""Analytics tool leveraging the published BigQuery Conversational Data Agent."""

import re
import time
from typing import Any, Dict
import google.auth
from google.auth.transport.requests import Request
from google.adk.tools import FunctionTool
from google.adk.tools.data_agent.data_agent_tool import ask_data_agent, DataAgentToolConfig

import logging

from app.config import get_data_agent_name

logger = logging.getLogger(__name__)

_creds = None

# BRD NFR-4.1 forbids exposing infrastructure internals in a user-facing answer,
# and the Data Agent emits fully-qualified `project.dataset.table` references.
# The dataset and table are the operationally meaningful part - only the project
# qualifier is redacted, so the operator can still audit which table and which
# partition window were scanned.
_QUALIFIED_TABLE = re.compile(
    r"`(?P<project>[a-z][a-z0-9-]{4,28})\.(?P<rest>[A-Za-z_]\w*\.[A-Za-z_]\w*)`"
)


def _redact_project(sql: str) -> str:
    """Rewrites `project.dataset.table` to `dataset.table` in generated SQL."""
    return _QUALIFIED_TABLE.sub(lambda m: f"`{m.group('rest')}`", sql)


def get_credentials():
    global _creds
    if _creds is None or not _creds.valid:
        _creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not _creds.valid:
        _creds.refresh(Request())
    return _creds

def cymbal_analytics_tool(query: str) -> str:
    """Answers analytical, financial, operational, and inventory questions across Cymbal Superstore BigQuery data using the published BigQuery Data Agent.
    
    CRITICAL: Always pass the user's natural language inquiry VERBATIM without stripping or modifying standardized enterprise business glossary terms (e.g. Net Transaction Revenue, Total On-Hand Inventory, Estimated Cover Hours, Cashier Manual Override Rate).
    
    Args:
        query: The natural language analytical question to ask the BigQuery Data Agent verbatim.
        
    Returns:
        A formatted string containing the answer, SQL query generated, and data summary.
    """
    settings = DataAgentToolConfig()
    max_retries = 3
    base_delay = 2.0
    last_error = None
    
    for attempt in range(max_retries):
        try:
            creds = get_credentials()
            result = ask_data_agent(
                data_agent_name=get_data_agent_name(),
                query=query,
                credentials=creds,
                settings=settings,
                tool_context=None
            )
            
            if result.get("status") == "SUCCESS":
                responses = result.get("response", [])
                final_text = ""
                sql_text = ""
                data_retrieved = None

                for item in responses:
                    if "text" in item and item["text"].get("textType") == "FINAL_RESPONSE":
                        final_text = "\n".join(item["text"].get("parts", []))
                    if "data" in item and "generatedSql" in item["data"]:
                        sql_text = item["data"]["generatedSql"]
                    # Superseded intermediate results are replaced by the literal
                    # string "Intermediate result omitted", so only accept a dict.
                    if isinstance(item.get("Data Retrieved"), dict):
                        data_retrieved = item["Data Retrieved"]

                output_parts = []
                if final_text:
                    output_parts.append(final_text)

                # The rows are appended unconditionally, NOT as a fallback for a
                # missing summary. The Data Agent's FINAL_RESPONSE is a narrative
                # ("here are the top 20 positions...") that names the result set
                # without containing it; returning only that narrative leaves the
                # coordinator with nothing to ground on, and it fabricates a
                # plausible table instead. Forwarding the rows is what makes the
                # coordinator's answer verifiable.
                if data_retrieved:
                    headers = data_retrieved.get("headers", [])
                    rows = data_retrieved.get("rows", [])
                    summary = data_retrieved.get("summary", f"Showing {len(rows)} rows.")
                    output_parts.append(
                        f"\n[Data Retrieved] {summary}\nColumns: {headers}"
                    )
                    for r in rows:
                        output_parts.append(str(r))
                elif not final_text:
                    output_parts.append("Query succeeded but returned no explicit final text.")

                if not data_retrieved:
                    # Makes the empty result explicit so the coordinator reports
                    # "no rows" rather than filling the gap from model memory.
                    output_parts.append(
                        "\n[Data Retrieved] NONE - the Data Agent returned no tabular "
                        "result for this question. Do not invent rows; report that no "
                        "data was returned and offer to refine the question."
                    )

                if sql_text:
                    output_parts.append(
                        f"\n[Generated SQL]:\n```sql\n{_redact_project(sql_text)}\n```"
                    )

                return "\n".join(output_parts)
            else:
                last_error = f"Data Agent status: {result.get('status')}, response: {result.get('response')}"
                logger.warning("Data Agent returned non-success status on attempt %d: %s", attempt + 1, last_error)
        except Exception as e:
            last_error = str(e)
            logger.warning("Error connecting to Data Agent on attempt %d: %s", attempt + 1, e)
            
        time.sleep(base_delay * (2 ** attempt))
        
    logger.error("All %d retries failed connecting to Data Agent. Last internal error: %s", max_retries, last_error)
    return "Store data is currently unreachable due to a temporary service failure. Please verify your query or try again later."

# Explicit FunctionTool instance as recommended by ADK guidelines
analytics_function_tool = FunctionTool(cymbal_analytics_tool)
