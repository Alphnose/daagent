"""ADK Coordinator Agent binding analytics, RAG, and Bigtable MCP toolsets.

Model and every GCP resource identifier are resolved through :mod:`app.config`, so
this module is portable across projects and environments without edits.

The module exposes two entry points:

``app``
    An :class:`~google.adk.apps.App` wrapping the coordinator agent together with the
    :class:`BigQueryAgentAnalyticsPlugin` telemetry plugin. ADK's agent loader prefers
    this symbol, so ``adk web app`` / ``adk run`` / ``agents-cli`` automatically stream
    every prompt, LLM response, tool invocation, latency and error into BigQuery.

``root_agent``
    The bare coordinator agent, kept for backwards compatibility and for callers that
    construct their own ``Runner``.
"""

import logging

from google.adk.agents import Agent
from google.adk.apps import App

from app.config import (
    get_bq_telemetry_dataset,
    get_bq_telemetry_location,
    get_bq_telemetry_table,
    get_gemini_model,
    get_project_id,
    is_telemetry_enabled,
)
from app.prompts import COORDINATOR_SYSTEM_INSTRUCTION
from app.tools.analytics_tool import cymbal_analytics_tool
from app.tools.bigtable_mcp_tool import bigtable_mcp_toolset
from app.tools.rag_tool import pos_troubleshooting_rag_tool

logger = logging.getLogger(__name__)

# ADK identifies an app by its agent directory name, and the FastAPI / Playground
# surfaces key sessions on that same name, so the App must be called "app" to match
# the `app/` package. The agent itself keeps its descriptive name.
APP_NAME = "app"
MODEL_NAME = get_gemini_model()

cymbal_operations_agent = Agent(
    name="cymbal_operations_agent",
    description="Operational coordinator agent for Cymbal Superstores managing analytics, POS diagnostics RAG, and real-time Bigtable auditing.",
    model=MODEL_NAME,
    instruction=COORDINATOR_SYSTEM_INSTRUCTION,
    tools=[
        cymbal_analytics_tool,
        pos_troubleshooting_rag_tool,
        bigtable_mcp_toolset,
    ],
)


def build_telemetry_plugin():
    """Builds the BigQuery Agent Analytics plugin, or ``None`` when disabled.

    The plugin streams session logs (prompts, LLM responses, tool arguments and
    results, latency, token usage, errors) into ``<project>.<dataset>.<table>`` through
    the BigQuery Storage Write API, which is asynchronous and therefore never blocks
    agent execution. Dataset, table, project and location are all resolved from the
    environment so the same code runs against any project.

    Returns ``None`` (with a warning) instead of raising when the plugin cannot be
    constructed, so a telemetry outage can never take the agent itself down.
    """
    if not is_telemetry_enabled():
        logger.info("BigQuery telemetry disabled via ENABLE_BQ_TELEMETRY.")
        return None

    try:
        from google.adk.plugins.bigquery_agent_analytics_plugin import (
            BigQueryAgentAnalyticsPlugin,
        )
    except ImportError:
        logger.warning(
            "BigQueryAgentAnalyticsPlugin unavailable. Install the extra with "
            "`pip install 'google-adk[bigquery-analytics]'` to enable telemetry."
        )
        return None

    try:
        plugin = BigQueryAgentAnalyticsPlugin(
            project_id=get_project_id(),
            dataset_id=get_bq_telemetry_dataset(),
            table_id=get_bq_telemetry_table(),
            location=get_bq_telemetry_location(),
        )
    except Exception as exc:  # pragma: no cover - depends on local credential state
        logger.warning("Failed to initialize BigQuery telemetry plugin: %s", exc)
        return None

    logger.info(
        "BigQuery telemetry enabled -> %s.%s.%s (%s)",
        get_project_id(),
        get_bq_telemetry_dataset(),
        get_bq_telemetry_table(),
        get_bq_telemetry_location(),
    )
    return plugin


_telemetry_plugin = build_telemetry_plugin()

# ADK's agent loader resolves `app` before `root_agent`, so the plugin is applied to
# every runtime surface (adk web / adk run / agents-cli / Agent Runtime) automatically.
app = App(
    name=APP_NAME,
    root_agent=cymbal_operations_agent,
    plugins=[_telemetry_plugin] if _telemetry_plugin else [],
)

# Expose root_agent for ADK Web UI / CLI and for callers building their own Runner.
root_agent = cymbal_operations_agent
