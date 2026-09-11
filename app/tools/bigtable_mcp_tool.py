"""Bigtable MCP Toolset integration via Cloud Run Database Toolbox microservice."""

import os
from typing import Optional
import google.auth
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from app.tools.bigtable_tool import read_cashier_realtime_metrics

BIGTABLE_MCP_URL = os.environ.get(
    "BIGTABLE_MCP_URL",
    "https://mcp-toolbox-bigtable-z3dyhmkzwa-uc.a.run.app"
)

def get_id_token(audience: str) -> str:
    """Generates a GCP OIDC ID Token for the Cloud Run service audience."""
    auth_req = Request()
    token = id_token.fetch_id_token(auth_req, audience)
    return token

def create_bigtable_mcp_toolset() -> McpToolset:
    """Instantiates the ADK McpToolset connecting to the Cloud Run Toolbox microservice via SSE JSON-RPC."""
    audience = BIGTABLE_MCP_URL.rstrip("/")
    mcp_endpoint = f"{audience}/mcp"
    
    headers = {}
    try:
        token = get_id_token(audience)
        headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass
        
    return McpToolset(
        connection_params={
            "url": mcp_endpoint,
            "headers": headers
        }
    )

# Callable tool interface for the coordinator agent
bigtable_mcp_toolset = read_cashier_realtime_metrics
