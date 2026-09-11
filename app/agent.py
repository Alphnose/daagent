"""ADK Coordinator Agent binding analytics, RAG, and Bigtable operational tools."""

import os
from google.adk.agents import Agent
from app.prompts import COORDINATOR_SYSTEM_INSTRUCTION
from app.tools.analytics_tool import cymbal_analytics_tool
from app.tools.rag_tool import pos_troubleshooting_rag_tool
from app.tools.bigtable_mcp_tool import bigtable_mcp_toolset

MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

cymbal_operations_agent = Agent(
    name="cymbal_operations_agent",
    description="Operational coordinator agent for Cymbal Superstores managing analytics, POS diagnostics RAG, and real-time Bigtable auditing.",
    model=MODEL_NAME,
    instruction=COORDINATOR_SYSTEM_INSTRUCTION,
    tools=[
        cymbal_analytics_tool,
        pos_troubleshooting_rag_tool,
        bigtable_mcp_toolset
    ]
)

# Expose root_agent for ADK Web UI / CLI
root_agent = cymbal_operations_agent
