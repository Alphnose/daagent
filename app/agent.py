"""ADK Coordinator Agent binding analytics, RAG, and Bigtable MCP toolsets.

Model and every GCP resource identifier are resolved through :mod:`app.config`, so
this module is portable across projects and environments without edits.
"""

from google.adk.agents import Agent

from app.config import get_gemini_model
from app.prompts import COORDINATOR_SYSTEM_INSTRUCTION
from app.tools.analytics_tool import cymbal_analytics_tool
from app.tools.bigtable_mcp_tool import bigtable_mcp_toolset
from app.tools.rag_tool import pos_troubleshooting_rag_tool

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

# Expose root_agent for ADK Web UI / CLI
root_agent = cymbal_operations_agent
