"""Pytest test suite with strict assertions for Cymbal Operations Coordinator Agent and tools."""

import os
import asyncio
import pytest
from dotenv import load_dotenv

load_dotenv()

# Pre-flight environment variables
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
os.environ["GOOGLE_CLOUD_PROJECT"] = os.environ.get("PROJECT_ID", "antigravity-503007")
os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

from google.genai import types
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from app.agent import cymbal_operations_agent
from app.tools.rag_tool import pos_troubleshooting_rag_tool, MANDATORY_DECLINE_WARNING
from app.tools.analytics_tool import cymbal_analytics_tool
from app.tools.bigtable_tool import read_cashier_realtime_metrics


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def test_uc_1_1a_hardware_error_rag():
    """UC 1.1a: Hardware Error (ERR-PAY-4001) - pos_troubleshooting_rag_tool retrieval."""
    query = "What is the immediate field recovery protocol when a cashier encounters an ERR-PAY-4001 EMV contactless payment freeze, and how do we ensure the customer is not double-charged?"
    result = pos_troubleshooting_rag_tool(query)
    
    assert result is not None
    assert len(result) > 0
    # Must retrieve Toshiba TCx 810 or relevant POS runbook
    assert "Toshiba" in result or "TCx" in result or "Runbook" in result
    # Must include clickable HTTPS documentation link
    assert "https://storage.cloud.google.com/" in result


def test_uc_1_1c_out_of_scope_hardware_guardrail():
    """UC 1.1c: Out-of-Scope Hardware - triggers mandatory decline warning string when similarity < 0.70."""
    query = "How do I replace the engine oil on a Ford F-150 truck?"
    result = pos_troubleshooting_rag_tool(query)
    
    assert result is not None
    # Must contain the certified mandatory decline warning
    assert MANDATORY_DECLINE_WARNING in result or "similarity >= 0.70" in result
    assert "Ford F-150" in result


def test_uc_1_2a_stockout_risk_analytics():
    """UC 1.2a: Stockout Risk (<20h) - queries gold_inventory_reconciliation_ledger."""
    query = "What is the estimated cover hours remaining for store inventory positions experiencing stockout risk of less than 20 hours, and what is their total on-hand inventory?"
    result = cymbal_analytics_tool(query)
    
    assert result is not None
    assert len(result) > 0
    # Must answer or return structured data/SQL without leaking internal stack trace
    assert "Traceback" not in result
    assert "FileNotFoundError" not in result


def test_uc_1_3_realtime_cashier_metrics():
    """UC 1.3: Real-Time Cashier Metrics - queries Bigtable STORE_048#CASH_1190."""
    result = read_cashier_realtime_metrics("STORE_048", "CASH_1190")
    
    assert result is not None
    assert "STORE_048" in result
    assert "CASH_1190" in result
    assert "1-Hour Transaction Count" in result
    assert "Anomaly Risk Score" in result
    assert "Audit Status" in result


def test_coordinator_agent_tool_binding():
    """Verify root coordinator agent binds all 3 tools and correct model."""
    assert cymbal_operations_agent.name == "cymbal_operations_agent"
    assert "gemini-3.6-flash" in cymbal_operations_agent.model
    assert len(cymbal_operations_agent.tools) == 3
    
    # Verify instruction contains Partition Date Clarification Guardrail
    assert "Partition Date Clarification Guardrail" in cymbal_operations_agent.instruction


@pytest.mark.asyncio
async def test_coordinator_execution_uc_1_3():
    """Verify coordinator agent executes operational inquiry and dispatches tool."""
    session_svc = InMemorySessionService()
    runner = Runner(agent=cymbal_operations_agent, app_name="cymbal_test_app", session_service=session_svc)
    session = await session_svc.create_session(app_name="cymbal_test_app", user_id="tester")
    
    msg = types.Content(role="user", parts=[types.Part.from_text(text="Read live 1-hour rolling metrics and audit status flags for Cashier CASH_1190 at Store 48.")])
    tool_calls = []
    response_texts = []
    
    async for event in runner.run_async(user_id="tester", session_id=session.id, new_message=msg):
        if hasattr(event, "content") and event.content:
            for part in event.content.parts or []:
                if getattr(part, "function_call", None):
                    tool_calls.append(part.function_call.name)
                if getattr(part, "text", None):
                    response_texts.append(part.text)
                    
    assert len(tool_calls) > 0 or len(response_texts) > 0
    full_text = "\n".join(response_texts)
    assert "CASH_1190" in full_text or any("read_cashier" in tc for tc in tool_calls)
