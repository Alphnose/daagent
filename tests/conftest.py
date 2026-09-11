"""Shared pytest fixtures and environment bootstrap for the agent test suite.

No GCP identifier is hardcoded here: the project is resolved from the environment
or Application Default Credentials, so the suite runs unchanged on any workstation,
CI runner or project.
"""

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from app.config import ConfigurationError, get_project_id  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: test requires live GCP resources and credentials"
    )


@pytest.fixture(scope="session", autouse=True)
def gcp_environment():
    """Configures Vertex AI env vars from the resolved project (skips when absent)."""
    try:
        project = get_project_id()
    except ConfigurationError as exc:
        pytest.skip(f"No GCP project configured for this environment: {exc}")

    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
    os.environ["GOOGLE_CLOUD_PROJECT"] = project
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
    return project


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT
