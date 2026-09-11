"""Pytest suite with strict assertions for the Cymbal Operations Coordinator Agent.

Environment bootstrap (project resolution, Vertex AI vars, sys.path) lives in
``tests/conftest.py`` so no test file hardcodes a GCP identifier.
"""

import pytest
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

from app.agent import cymbal_operations_agent
from app.tools.analytics_tool import cymbal_analytics_tool
from app.tools.bigtable_mcp_tool import bigtable_mcp_toolset
from app.tools.bigtable_tool import normalize_row_key_prefix, read_cashier_realtime_metrics
from app.tools.rag_tool import MANDATORY_DECLINE_WARNING, pos_troubleshooting_rag_tool

# Leaked-infrastructure markers that must never reach an end user.
FORBIDDEN_LEAKS = (
    "Traceback (most recent call last)",
    "gserviceaccount.com",
    "google.api_core.exceptions",
    "grpc._channel",
    "/usr/local/google/home",
)


def assert_no_infrastructure_leak(text: str) -> None:
    for marker in FORBIDDEN_LEAKS:
        assert marker not in text, f"Infrastructure detail leaked to the user: {marker}"


# --------------------------------------------------------------------------- #
# UC 1.1a / 1.1c - POS troubleshooting RAG                                      #
# --------------------------------------------------------------------------- #

@pytest.mark.integration
def test_uc_1_1a_hardware_error_rag():
    """UC 1.1a: ERR-PAY-4001 must return certified runbook content with a GCS link."""
    query = (
        "What is the immediate field recovery protocol when a cashier encounters an "
        "ERR-PAY-4001 EMV contactless payment freeze, and how do we ensure the customer "
        "is not double-charged?"
    )
    result = pos_troubleshooting_rag_tool(query)

    assert result
    assert_no_infrastructure_leak(result)
    assert MANDATORY_DECLINE_WARNING not in result, "Certified runbook unexpectedly declined"
    assert "Toshiba" in result or "TCx" in result or "Runbook" in result
    assert "https://storage.cloud.google.com/" in result


@pytest.mark.integration
def test_uc_1_1c_out_of_scope_returns_exact_mandatory_warning():
    """UC 1.1c: below-threshold queries must return the EXACT specified warning string."""
    result = pos_troubleshooting_rag_tool("How do I replace the engine oil on a Ford F-150 truck?")

    assert result
    assert_no_infrastructure_leak(result)
    # Exact string match - no paraphrasing of the certified decline contract.
    assert result.startswith(MANDATORY_DECLINE_WARNING)
    assert "0.70 similarity threshold" in result
    assert "out of scope" in result.lower()


def test_mandatory_decline_warning_contract():
    """The decline string is part of the operational contract and must stay stable."""
    assert MANDATORY_DECLINE_WARNING == (
        "WARNING: UNCERTIFIED RESULT - No certified POS troubleshooting documentation "
        "matched this query above the 0.70 similarity threshold. This request appears "
        "to be out of scope for Cymbal Superstore POS operations. No troubleshooting "
        "guidance can be provided."
    )


# --------------------------------------------------------------------------- #
# UC 1.2a - BigQuery Conversational Data Agent                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.integration
def test_uc_1_2a_stockout_risk_analytics():
    """UC 1.2a: stockout risk (<20h cover hours) analytical dispatch."""
    query = (
        "What is the estimated cover hours remaining for store inventory positions "
        "experiencing stockout risk of less than 20 hours, and what is their total "
        "on-hand inventory?"
    )
    result = cymbal_analytics_tool(query)

    assert result
    assert_no_infrastructure_leak(result)


# --------------------------------------------------------------------------- #
# UC 1.3 - Real-time Bigtable metrics                                           #
# --------------------------------------------------------------------------- #

def test_row_key_prefix_normalization():
    assert normalize_row_key_prefix("48", "1190") == "STORE_048#CASH_1190"
    assert normalize_row_key_prefix("STORE_048", "CASH_1190") == "STORE_048#CASH_1190"


@pytest.mark.integration
def test_uc_1_3_realtime_cashier_metrics():
    """UC 1.3: live cashier metrics for STORE_048#CASH_1190."""
    result = read_cashier_realtime_metrics("STORE_048", "CASH_1190")

    assert result
    assert_no_infrastructure_leak(result)
    assert "STORE_048" in result
    assert "CASH_1190" in result
    assert "1-Hour Transaction Count" in result
    assert "Anomaly Risk Score" in result
    assert "Audit Status" in result


# --------------------------------------------------------------------------- #
# Coordinator binding & guardrails                                              #
# --------------------------------------------------------------------------- #

def test_coordinator_agent_tool_binding():
    """The root agent binds all 3 toolsets and the configured coordinator model."""
    from app.config import get_gemini_model

    assert cymbal_operations_agent.name == "cymbal_operations_agent"
    assert cymbal_operations_agent.model == get_gemini_model()
    assert len(cymbal_operations_agent.tools) == 3

    assert cymbal_analytics_tool in cymbal_operations_agent.tools
    assert pos_troubleshooting_rag_tool in cymbal_operations_agent.tools
    assert bigtable_mcp_toolset in cymbal_operations_agent.tools


def test_coordinator_prompt_declares_required_guardrails():
    instruction = cymbal_operations_agent.instruction
    assert "Partition Date Clarification Guardrail" in instruction
    assert "Out-of-Scope" in instruction
    # Dynamic partition bounds must be expressed, not a frozen literal date.
    assert "CURRENT_DATE" in instruction or "rolling" in instruction.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_coordinator_execution_uc_1_3():
    """The coordinator dispatches a tool call for a live cashier metrics inquiry."""
    session_svc = InMemorySessionService()
    runner = Runner(
        agent=cymbal_operations_agent,
        app_name="cymbal_test_app",
        session_service=session_svc,
    )
    session = await session_svc.create_session(app_name="cymbal_test_app", user_id="tester")

    msg = types.Content(
        role="user",
        parts=[
            types.Part.from_text(
                text="Read live 1-hour rolling metrics and audit status flags for Cashier CASH_1190 at Store 48."
            )
        ],
    )
    tool_calls, response_texts = [], []

    async for event in runner.run_async(
        user_id="tester", session_id=session.id, new_message=msg
    ):
        if getattr(event, "content", None):
            for part in event.content.parts or []:
                if getattr(part, "function_call", None):
                    tool_calls.append(part.function_call.name)
                if getattr(part, "text", None):
                    response_texts.append(part.text)

    full_text = "\n".join(response_texts)
    assert tool_calls, "Coordinator did not dispatch any tool"
    assert any("cashier" in name.lower() for name in tool_calls)
    assert_no_infrastructure_leak(full_text)
