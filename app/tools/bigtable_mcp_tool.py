"""Bigtable MCP Toolset backed by the Cloud Run Database Toolbox microservice.

Architecture
------------
The agent consumes Bigtable **exclusively through the declarative MCP contract**
defined in ``tools.yaml`` and served by the ``mcp-toolbox-bigtable`` Cloud Run
microservice (official ``database-toolbox/toolbox`` image):

    ADK Agent -> McpToolset (JSON-RPC / Streamable HTTP + OIDC)
              -> Cloud Run Toolbox -> Bigtable GoogleSQL

Two historical defects are fixed here:

1. **Path routing** - the Toolbox exposes MCP at ``/mcp`` (Streamable HTTP) and
   ``/mcp/sse`` (legacy SSE). The previous implementation targeted ``/sse``,
   which returns HTTP 404, causing an immediate silent fallback.
2. **OIDC authorization** - the ID token is now minted per request through
   ``header_provider`` (so it never goes stale mid-session) and resolves through
   a chain of strategies that works on Cloud Run, GCE/GKE, service accounts and
   local developer machines (``gcloud auth print-identity-token``).

The direct Bigtable client tool is retained **only** as an explicit, loudly
logged emergency fallback for environments where the microservice is not
reachable (e.g. offline CI, VPC-SC restricted sandboxes). Set
``BIGTABLE_MCP_REQUIRED=true`` to disable the fallback and fail hard instead.
"""

import base64
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple

from google.adk.tools import BaseTool, FunctionTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from app.config import get_bigtable_mcp_url
from app.tools.bigtable_tool import read_cashier_realtime_metrics

logger = logging.getLogger(__name__)

# MCP JSON-RPC route exposed by the Database Toolbox container.
MCP_ROUTE = "/mcp"
# Toolset declared in tools.yaml; used to scope the served tools when present.
MCP_TOOLSET_NAME = os.environ.get("BIGTABLE_MCP_TOOLSET", "cymbal_operations")
# ID tokens are valid for 1h; refresh well before expiry.
_TOKEN_TTL_SECONDS = 45 * 60

_token_lock = threading.Lock()
# audience -> (id_token, minted_at_epoch)
_token_cache: Dict[str, Tuple[Optional[str], float]] = {}
# audience -> name of the credential strategy Cloud Run accepted most recently
_preferred_strategy: Dict[str, str] = {}


def _decode_jwt_audience(token: str) -> Optional[str]:
    """Reads the ``aud`` claim from an ID token without verifying the signature."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64)).get("aud")
    except Exception:
        return None


def _fetch_id_token_via_adc(audience: str) -> Optional[str]:
    """Mints an OIDC ID token from ADC (service account key or metadata server)."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        return id_token.fetch_id_token(Request(), audience)
    except Exception as exc:
        logger.debug("ADC ID token strategy unavailable for %s: %s", audience, exc)
        return None


def _fetch_id_token_via_gcloud(audience: Optional[str] = None) -> Optional[str]:
    """Falls back to the developer's gcloud identity token (local workstations)."""
    gcloud = shutil.which("gcloud")
    if not gcloud:
        return None
    command = [gcloud, "auth", "print-identity-token"]
    if audience:
        command.append(f"--audiences={audience}")
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=30, check=False
        )
        token = result.stdout.strip()
        if not token:
            logger.debug(
                "gcloud ID token strategy failed (%s): %s",
                " ".join(command[1:]),
                result.stderr.strip()[:200],
            )
        return token or None
    except Exception as exc:
        logger.debug("gcloud ID token strategy unavailable: %s", exc)
        return None


def _probe_endpoint(audience: str, token: str) -> bool:
    """Returns True when Cloud Run IAM accepts this token for the target service.

    Selecting a credential purely by its ``aud`` claim is not sufficient: the
    identity behind the token must also hold ``roles/run.invoker``. On managed
    workstations, for example, ADC often resolves to a shared service account that
    can mint a correctly scoped token yet is denied by IAM (HTTP 403). Probing keeps
    the MCP path alive instead of silently degrading to the local Bigtable client.
    """
    try:
        import httpx

        response = httpx.get(
            f"{audience}/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            follow_redirects=False,
        )
        return response.status_code not in (401, 403)
    except Exception as exc:
        logger.debug("Credential probe against %s could not complete: %s", audience, exc)
        # Network problems are not an authorization verdict - keep the candidate.
        return True


def _id_token_strategies(audience: str):
    """Ordered ID token strategies, each yielding ``(name, token)``."""
    yield "adc", _fetch_id_token_via_adc(audience)
    yield "gcloud --audiences", _fetch_id_token_via_gcloud(audience)
    yield "gcloud", _fetch_id_token_via_gcloud()


def _mint_id_token(audience: str) -> Optional[str]:
    """Returns the first ID token that Cloud Run actually accepts for ``audience``."""
    candidates = [(name, token) for name, token in _id_token_strategies(audience) if token]
    if not candidates:
        return None

    # Fast path: reuse the strategy that authenticated successfully for this endpoint.
    preferred = _preferred_strategy.get(audience)
    if preferred:
        for name, token in candidates:
            if name == preferred:
                return token

    for name, token in candidates:
        if _probe_endpoint(audience, token):
            if preferred != name:
                logger.info("Authenticating to the Bigtable MCP endpoint via %s.", name)
            _preferred_strategy[audience] = name
            return token

    logger.error(
        "Every OIDC credential was rejected by %s. Grant roles/run.invoker on the "
        "MCP service to the identity running this agent.",
        audience,
    )
    return candidates[0][1]


def get_id_token(audience: str, force_refresh: bool = False) -> Optional[str]:
    """Returns a cached OIDC ID token for the given Cloud Run service audience.

    The cache is keyed by audience: a token minted for one endpoint is never reused
    against another.
    """
    with _token_lock:
        cached, minted_at = _token_cache.get(audience, (None, 0.0))
        if cached and not force_refresh and (time.time() - minted_at) < _TOKEN_TTL_SECONDS:
            return cached

        token = _mint_id_token(audience)
        if token:
            _token_cache[audience] = (token, time.time())
        else:
            logger.warning(
                "Unable to mint an OIDC ID token for %s. Authenticate with "
                "`gcloud auth login` or run with a service account identity.",
                audience,
            )
        return token


def build_auth_headers(base_url: str) -> Dict[str, str]:
    """Builds the ``Authorization: Bearer <OIDC_TOKEN>`` header for Cloud Run."""
    token = get_id_token(base_url)
    return {"Authorization": f"Bearer {token}"} if token else {}


def create_bigtable_mcp_toolset(base_url: Optional[str] = None) -> McpToolset:
    """Instantiates the ADK ``McpToolset`` bound to the Database Toolbox service.

    The OIDC bearer token is supplied twice on purpose:

    * ``connection_params.headers`` - ADK performs the MCP *session handshake* with
      these headers only. Without them Cloud Run rejects the handshake with 403 and
      the agent silently degrades to the local client.
    * ``header_provider`` - re-evaluated for subsequent requests on the session.

    Args:
        base_url: Optional override of the Cloud Run service base URL. Defaults to
            the ``BIGTABLE_MCP_URL`` environment variable resolved by ``app.config``.

    Raises:
        ValueError: if no MCP endpoint is configured for the environment.
    """
    resolved = (base_url or get_bigtable_mcp_url() or "").rstrip("/")
    if not resolved:
        raise ValueError(
            "BIGTABLE_MCP_URL is not configured. Deploy the Database Toolbox "
            "microservice (`make mcp-deploy`) and export its Cloud Run URL."
        )

    def header_provider(_readonly_context=None) -> Dict[str, str]:
        return build_auth_headers(resolved)

    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=f"{resolved}{MCP_ROUTE}",
            headers=build_auth_headers(resolved),
            timeout=30,
        ),
        header_provider=header_provider,
        tool_list_cache_ttl_seconds=300,
    )


class BigtableOperationsToolset(BaseToolset):
    """ADK Toolset exposing the declarative Bigtable MCP tools to the agent.

    Primary path: the ``mcp-toolbox-bigtable`` Cloud Run microservice.
    Fallback path: the native Bigtable client tool (disabled when
    ``BIGTABLE_MCP_REQUIRED=true``).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mcp_toolset: Optional[McpToolset] = None
        self._mcp_toolset_created_at: float = 0.0
        self._mcp_init_error: Optional[str] = None
        self._direct_tool = FunctionTool(read_cashier_realtime_metrics)
        self._require_mcp = os.environ.get(
            "BIGTABLE_MCP_REQUIRED", "false"
        ).lower() in {"1", "true", "yes"}

    @property
    def mcp_enabled(self) -> bool:
        """True when the last MCP handshake succeeded (no fallback in effect)."""
        return self._mcp_toolset is not None and self._mcp_init_error is None

    async def _ensure_mcp_toolset(self) -> Optional[McpToolset]:
        """Returns a toolset whose handshake headers carry a non-expired ID token."""
        stale = (time.time() - self._mcp_toolset_created_at) >= _TOKEN_TTL_SECONDS
        if self._mcp_toolset is not None and not stale:
            return self._mcp_toolset

        if self._mcp_toolset is not None:
            await self._close_mcp_toolset()

        try:
            self._mcp_toolset = create_bigtable_mcp_toolset()
            self._mcp_toolset_created_at = time.time()
            self._mcp_init_error = None
        except Exception as exc:
            self._mcp_toolset = None
            self._mcp_init_error = str(exc)
            logger.warning("Bigtable MCP toolset could not be initialized: %s", exc)
        return self._mcp_toolset

    async def get_tools(self, readonly_context=None) -> List[BaseTool]:
        toolset = await self._ensure_mcp_toolset()
        if toolset is not None:
            try:
                mcp_tools = await toolset.get_tools(readonly_context)
                if mcp_tools:
                    logger.info(
                        "Loaded %d tool(s) from the Bigtable MCP microservice: %s",
                        len(mcp_tools),
                        [t.name for t in mcp_tools],
                    )
                    return mcp_tools
                self._mcp_init_error = "empty tool list"
                logger.error(
                    "Bigtable MCP microservice returned an EMPTY tool list. The "
                    "deployed tools.yaml secret is likely missing its `tools:` "
                    "section - redeploy with `make mcp-deploy`."
                )
            except Exception as exc:
                self._mcp_init_error = str(exc)
                logger.error(
                    "Bigtable MCP microservice call failed (endpoint/auth issue): %s",
                    exc,
                )

        if self._require_mcp:
            raise RuntimeError(
                "BIGTABLE_MCP_REQUIRED=true but the Bigtable MCP microservice is "
                f"unavailable ({self._mcp_init_error or 'empty or failing tool list'})."
            )

        logger.warning(
            "Degrading to the direct Bigtable client tool. Real-time cashier "
            "metrics remain available but the declarative MCP contract is bypassed."
        )
        return [self._direct_tool]

    async def _close_mcp_toolset(self) -> None:
        try:
            await self._mcp_toolset.close()
        except Exception as exc:  # pragma: no cover - best effort cleanup
            logger.debug("Error while closing the Bigtable MCP toolset: %s", exc)
        finally:
            self._mcp_toolset = None
            self._mcp_toolset_created_at = 0.0

    async def close(self) -> None:
        if self._mcp_toolset is not None:
            await self._close_mcp_toolset()

# ADK Toolset instance directly bound to cymbal_operations_agent.tools
bigtable_mcp_toolset = BigtableOperationsToolset()
