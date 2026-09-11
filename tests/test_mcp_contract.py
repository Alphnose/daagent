"""MCP architecture tests.

Regression guard for the audit finding
*"the Model Context Protocol (MCP) server is bypassed in practice because OIDC
authorization and path routing discrepancies result in immediate fallbacks to a
direct gRPC database client scan, rendering the declarative tooling settings dead
configuration."*

Structural tests always run. Live-contract tests are marked ``integration`` and skip
gracefully when the Cloud Run endpoint or credentials are unavailable.
"""

import json

import pytest
import yaml

from app.config import get_bigtable_mcp_url
from app.tools import bigtable_mcp_tool
from app.tools.bigtable_mcp_tool import (
    MCP_ROUTE,
    BigtableOperationsToolset,
    bigtable_mcp_toolset,
    create_bigtable_mcp_toolset,
)


# --------------------------------------------------------------------------- #
# Structural / routing tests                                                    #
# --------------------------------------------------------------------------- #

def test_mcp_route_targets_the_jsonrpc_endpoint():
    """Toolbox serves MCP at /mcp; the legacy /sse root path returns HTTP 404."""
    assert MCP_ROUTE == "/mcp"


def test_toolset_url_is_built_from_configuration():
    toolset = create_bigtable_mcp_toolset("https://example-toolbox.a.run.app/")
    url = toolset._mcp_session_manager._connection_params.url
    assert url == "https://example-toolbox.a.run.app/mcp"


def test_toolset_requires_configured_endpoint(monkeypatch):
    monkeypatch.setattr(bigtable_mcp_tool, "get_bigtable_mcp_url", lambda: None)
    with pytest.raises(ValueError) as excinfo:
        create_bigtable_mcp_toolset()
    assert "BIGTABLE_MCP_URL" in str(excinfo.value)


def test_auth_headers_use_oidc_bearer_scheme(monkeypatch):
    monkeypatch.setattr(bigtable_mcp_tool, "get_id_token", lambda audience: "fake-oidc-token")
    headers = bigtable_mcp_tool.build_auth_headers("https://example-toolbox.a.run.app")
    assert headers == {"Authorization": "Bearer fake-oidc-token"}


def test_agent_binds_the_mcp_toolset_not_the_raw_function():
    from app.agent import cymbal_operations_agent

    assert bigtable_mcp_toolset in cymbal_operations_agent.tools
    assert isinstance(bigtable_mcp_toolset, BigtableOperationsToolset)


def test_declarative_contract_defines_tools_and_toolsets(project_root):
    """tools.yaml must not be dead config: it declares sources, tools and toolsets."""
    raw = (project_root / "tools.yaml").read_text()
    parsed = yaml.safe_load(raw)

    assert "sources" in parsed and parsed["sources"], "tools.yaml declares no sources"
    assert "tools" in parsed and parsed["tools"], "tools.yaml declares no tools"
    assert "toolsets" in parsed and parsed["toolsets"], "tools.yaml declares no toolsets"

    tool = parsed["tools"]["read_cashier_realtime_metrics"]
    assert tool["kind"] == "bigtable-sql"
    assert tool["source"] in parsed["sources"]
    assert tool["statement"].strip(), "bigtable-sql tool has no statement"
    assert tool["parameters"], "bigtable-sql tool declares no parameters"
    assert "@row_key_prefix" in tool["statement"]


# --------------------------------------------------------------------------- #
# Live contract tests (require Cloud Run + credentials)                         #
# --------------------------------------------------------------------------- #

def _skip_without_endpoint():
    if not get_bigtable_mcp_url():
        pytest.skip("BIGTABLE_MCP_URL is not configured in this environment.")


@pytest.mark.integration
def test_oidc_token_can_be_minted():
    _skip_without_endpoint()
    token = bigtable_mcp_tool.get_id_token(get_bigtable_mcp_url(), force_refresh=True)
    if not token:
        pytest.skip("No OIDC credentials available on this host.")
    assert token.count(".") == 2, "Expected a JWT-formatted OIDC ID token"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_mcp_microservice_serves_the_declared_tool():
    """The agent must obtain its Bigtable tool from MCP, not the local fallback.

    The fallback ``FunctionTool`` deliberately carries the same tool name, so this
    test asserts on the concrete type to prove the MCP path is live.
    """
    from google.adk.tools.mcp_tool.mcp_tool import McpTool

    _skip_without_endpoint()
    toolset = BigtableOperationsToolset()
    try:
        tools = await toolset.get_tools()
        names = [t.name for t in tools]
        assert "read_cashier_realtime_metrics" in names
        assert all(isinstance(t, McpTool) for t in tools), (
            "Agent degraded to the local Bigtable client instead of the MCP contract: "
            f"{[type(t).__name__ for t in tools]}"
        )
        assert toolset.mcp_enabled
    finally:
        await toolset.close()


@pytest.mark.integration
def test_live_mcp_tool_call_returns_realtime_metrics():
    """The declared statement executes server-side and returns live Bigtable telemetry.

    Executed over raw JSON-RPC (using this module's own OIDC headers) because ADK's
    ``McpTool.run_async`` requires a live agent invocation context; agent-level
    dispatch is covered by ``test_coordinator_execution_uc_1_3``.
    """
    import httpx

    _skip_without_endpoint()
    base_url = get_bigtable_mcp_url()
    headers = {
        **bigtable_mcp_tool.build_auth_headers(base_url),
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    response = httpx.post(
        f"{base_url}{MCP_ROUTE}",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "read_cashier_realtime_metrics",
                "arguments": {"row_key_prefix": "STORE_048#CASH_1190"},
            },
        },
        timeout=60,
    )

    assert response.status_code == 200, f"MCP call rejected: {response.status_code}"
    body = response.json()
    assert "result" in body, f"MCP returned an error envelope: {body}"
    assert not body["result"].get("isError"), f"Tool execution failed: {body}"

    payload = json.dumps(body["result"])
    assert "STORE_048#CASH_1190" in payload
    assert "cashier_1h_txn_count" in payload
    assert "audit_status" in payload
