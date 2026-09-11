#!/usr/bin/env python3
"""Provision and query the BigQuery Conversational Agent over `agent_telemetry`.

Day 4 / Module 3 Challenge 4.1 asks for a BigQuery Data Agent scoped to the
telemetry dataset produced by `BigQueryAgentAnalyticsPlugin`, then four
"query recipes" answered through it rather than through hand-written SQL.

BigQuery Studio can create the same agent from the UI, but doing it here keeps
the artefact reproducible and reviewable: the table scope, the system
instruction and the exact recipe prompts are all in version control, and the
generated GoogleSQL can be captured for the acceptance evidence.

The Gemini Data Analytics REST surface is called directly because the
`google-cloud-geminidataanalytics` client is not a dependency of the agent
runtime and is not worth pulling in for a one-shot provisioning script.

Usage:
    python scripts/telemetry_data_agent.py create
    python scripts/telemetry_data_agent.py ask            # runs all 4 recipes
    python scripts/telemetry_data_agent.py ask --recipe 2
    python scripts/telemetry_data_agent.py ask --question "..."
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable

import google.auth
import google.auth.transport.requests
import requests

API_ROOT = "https://geminidataanalytics.googleapis.com/v1beta"
# Data Agents are a global resource even when the underlying data is regional.
AGENT_LOCATION = "global"
DEFAULT_AGENT_ID = "cymbal-agent-telemetry-data-agent"

# The four Challenge 4.1 recipes, verbatim from the lab instructions.
RECIPES: list[tuple[str, str]] = [
    (
        "Cost & Token Analysis",
        "Aggregate total input tokens and output tokens and request count "
        "grouped by model (model).",
    ),
    (
        "Tool Performance & Latency",
        "Calculate the average and maximum execution latency per tool, sorted "
        "by the slowest tools first.",
    ),
    (
        "Reliability & Error Analysis",
        "Find all failed tool calls or sessions with errors, showing the "
        "session ID, tool name, and error message.",
    ),
    (
        "Tool Invocations Distribution",
        "Show the top 3 most frequently invoked tools and their percentage "
        "distribution.",
    ),
]

SYSTEM_INSTRUCTION = """\
You are the operational observability analyst for the Cymbal Superstores
Operations Coordinator Agent. The `agent_telemetry` dataset is written by the
ADK `BigQueryAgentAnalyticsPlugin` via the BigQuery Storage Write API.

Schema orientation:
- `events` is the single wide append-only fact table. Every row is one
  lifecycle event; `event_type` discriminates the payload and the typed
  columns are only populated for the event types that produce them.
- The `v_*` views are the supported query surface: each one pre-filters
  `events` to a single `event_type` and projects only the columns that are
  meaningful for it. Prefer a view over re-deriving the filter by hand.

View routing:
- Token and model usage      -> `v_llm_response` (input_tokens, output_tokens, model)
- Tool latency and arguments -> `v_tool_completed` (tool_name, duration_ms)
- Tool failures              -> `v_tool_error` (tool_name, error_message)
- Turn / session errors       -> `v_invocation_error`, `v_agent_error`
- End-user turns              -> `v_user_message_received`

Query rules:
- `events` is DAY-partitioned on `timestamp` and clustered on
  (event_type, agent, user_id). Always apply a bound on `timestamp` so the
  query prunes partitions; default to the last 7 days when the user does not
  say otherwise, and state the window you applied.
- Latency is already materialised in milliseconds as `duration_ms`; never
  recompute it by subtracting timestamps across rows.
- Report percentages with two decimal places and round latencies to whole
  milliseconds.
"""


def _project_id() -> str:
    project = os.environ.get("PROJECT_ID") or os.environ.get(
        "GOOGLE_CLOUD_PROJECT"
    )
    if not project:
        _, project = google.auth.default()
    if not project:
        raise SystemExit("PROJECT_ID is not set and ADC carries no default project.")
    return project


def _session() -> requests.Session:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        }
    )
    return session


def _list_dataset_objects(project: str, dataset: str) -> list[dict[str, str]]:
    """Every table AND view in the dataset, as BigQuery table references.

    Challenge 4.1 scopes the agent to "all tables in the `agent_telemetry`
    dataset". The 25 `v_*` views are the readable surface over the single
    `events` fact table, so both are included; the Data Agent treats a view
    exactly like a table.
    """
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    return [
        {"projectId": project, "datasetId": dataset, "tableId": item.table_id}
        for item in client.list_tables(f"{project}.{dataset}")
    ]


def create(args: argparse.Namespace) -> int:
    project = _project_id()
    dataset = os.environ.get("BQ_TELEMETRY_DATASET", "agent_telemetry")
    tables = _list_dataset_objects(project, dataset)
    print(f"Scoping data agent to {len(tables)} object(s) in {project}.{dataset}")

    parent = f"projects/{project}/locations/{AGENT_LOCATION}"
    payload: dict[str, Any] = {
        "display_name": "Cymbal Agent Telemetry Analyst",
        "description": (
            "Conversational analytics over the ADK BigQuery Agent Analytics "
            "telemetry emitted by the Cymbal Operations Coordinator Agent."
        ),
        "data_analytics_agent": {
            "published_context": {
                "datasource_references": {"bq": {"table_references": tables}},
                "system_instruction": SYSTEM_INSTRUCTION,
            }
        },
    }

    session = _session()
    response = session.post(
        f"{API_ROOT}/{parent}/dataAgents",
        params={"data_agent_id": args.agent_id},
        data=json.dumps(payload),
        timeout=120,
    )
    if response.status_code == 409:
        print(f"Data agent '{args.agent_id}' already exists; patching context.")
        response = session.patch(
            f"{API_ROOT}/{parent}/dataAgents/{args.agent_id}",
            params={
                "updateMask": "data_analytics_agent,description,display_name"
            },
            data=json.dumps(payload),
            timeout=120,
        )
    if not response.ok:
        print(f"HTTP {response.status_code}\n{response.text}", file=sys.stderr)
        return 1

    print(json.dumps(response.json(), indent=2)[:1500])
    print(f"\nData agent: {parent}/dataAgents/{args.agent_id}")
    return 0


def _iter_chat_messages(raw: str) -> Iterable[dict[str, Any]]:
    """Yield the message objects out of the streaming `:chat` response.

    The endpoint answers with a JSON array that is flushed incrementally, so
    the body is parsed once at the end rather than per chunk.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    for chunk in parsed:
        for message in chunk.get("messages", [chunk]):
            yield message


def _render(message: dict[str, Any]) -> None:
    system = message.get("systemMessage") or {}
    if "text" in system:
        for part in system["text"].get("parts", []):
            print(part)
    if "schema" in system:
        query = system["schema"].get("query", {})
        if query.get("question"):
            print(f"  [schema resolution] {query['question']}")
    if "data" in system:
        data = system["data"]
        if "generatedSql" in data:
            print("\n  --- Generated GoogleSQL ---")
            for line in data["generatedSql"].splitlines():
                print(f"  {line}")
        if "result" in data:
            rows = data["result"].get("data", [])
            print(f"\n  --- Result rows: {len(rows)} ---")
            for row in rows[:20]:
                print(f"  {row}")
    if "error" in message:
        print(f"  [error] {message['error']}", file=sys.stderr)


def ask(args: argparse.Namespace) -> int:
    project = _project_id()
    parent = f"projects/{project}/locations/{AGENT_LOCATION}"
    session = _session()

    if args.question:
        questions = [("Ad-hoc", args.question)]
    elif args.recipe:
        questions = [RECIPES[args.recipe - 1]]
    else:
        questions = RECIPES

    for title, question in questions:
        print(f"\n{'=' * 78}\n[{title}] {question}\n{'=' * 78}")
        payload = {
            "parent": parent,
            "messages": [{"userMessage": {"text": question}}],
            "data_agent_context": {
                "data_agent": f"{parent}/dataAgents/{args.agent_id}"
            },
        }
        response = session.post(
            f"{API_ROOT}/{parent}:chat",
            data=json.dumps(payload),
            timeout=300,
        )
        if not response.ok:
            print(f"HTTP {response.status_code}\n{response.text}", file=sys.stderr)
            return 1
        for message in _iter_chat_messages(response.text):
            _render(message)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("create", help="Create or update the telemetry data agent.")

    ask_parser = sub.add_parser("ask", help="Run the Challenge 4.1 query recipes.")
    ask_parser.add_argument(
        "--recipe", type=int, choices=range(1, len(RECIPES) + 1), default=None
    )
    ask_parser.add_argument("--question", default=None)

    args = parser.parse_args()
    return {"create": create, "ask": ask}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
