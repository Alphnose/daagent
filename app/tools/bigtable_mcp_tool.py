"""Bigtable MCP Toolset integration via Cloud Run Database Toolbox microservice."""

import os
import logging
from typing import Optional, List
import google.auth
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools import BaseTool, FunctionTool
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, SseConnectionParams
from app.tools.bigtable_tool import read_cashier_realtime_metrics

logger = logging.getLogger(__name__)

BIGTABLE_MCP_URL = os.environ.get(
    "BIGTABLE_MCP_URL",
    "https://mcp-toolbox-bigtable-z3dyhmkzwa-uc.a.run.app"
)

def get_id_token(audience: str) -> Optional[str]:
    """Generates a GCP OIDC ID Token for the Cloud Run service audience."""
    try:
        auth_req = Request()
        token = id_token.fetch_id_token(auth_req, audience)
        return token
    except Exception as e:
        logger.warning("Failed to fetch OIDC ID token for audience %s: %s", audience, e)
        return None

def create_bigtable_mcp_toolset() -> McpToolset:
    """Instantiates the ADK McpToolset connecting to the Cloud Run Database Toolbox microservice via SSE JSON-RPC."""
    audience = BIGTABLE_MCP_URL.rstrip("/")
    sse_endpoint = f"{audience}/sse"
    
    headers = {}
    token = get_id_token(audience)
    if token:
        headers["Authorization"] = f"Bearer {token}"
        
    return McpToolset(
        connection_params=SseConnectionParams(
            url=sse_endpoint,
            headers=headers
        )
    )

class BigtableOperationsToolset(BaseToolset):
    """ADK Toolset exposing Bigtable operational inspection tools.
    
    Tries the Cloud Run Database Toolbox McpToolset endpoint first.
    If the remote MCP microservice is inaccessible due to VPC-SC/IAM restrictions,
    seamlessly falls back to the native Bigtable operational inspection tool.
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mcp_toolset = create_bigtable_mcp_toolset()
        self._direct_tool = FunctionTool(read_cashier_realtime_metrics)
        
    async def get_tools(self, readonly_context=None) -> List[BaseTool]:
        try:
            mcp_tools = await self._mcp_toolset.get_tools(readonly_context)
            if mcp_tools:
                return mcp_tools
        except Exception as e:
            logger.info("Cloud Run MCP endpoint unavailable, utilizing Bigtable direct operational tool: %s", e)
        return [self._direct_tool]

# ADK Toolset instance directly bound to cymbal_operations_agent.tools
bigtable_mcp_toolset = BigtableOperationsToolset()
