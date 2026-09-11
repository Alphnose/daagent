"""Portability tests: no environment-specific identifier may be hardcoded in source.

These tests are the regression guard for the audit finding
*"GCP Project ID is hardcoded across multiple core execution scripts and
configurations, violating the environment isolation principle and causing
immediate test failures on foreign systems."*
"""

import re
from pathlib import Path

import pytest

from app import config

# Files that legitimately carry a concrete project value (developer-local, gitignored)
_ALLOWED_FILES = {".env"}

# A GCP project id: 6-30 chars, lowercase letters/digits/hyphens, must contain a hyphen
# or digit to avoid matching ordinary words. Scanned only inside quoted literals.
_PROJECT_LITERAL = re.compile(
    r"""["'](?P<value>(?=[a-z0-9-]{6,30}["'])[a-z][a-z0-9]*(?:-[a-z0-9]+){1,4}[0-9]{3,})["']"""
)

_SOURCE_GLOBS = ("app/**/*.py", "tests/**/*.py", "*.py", "*.yaml", "Dockerfile", "Makefile")


def _iter_source_files(project_root: Path):
    for pattern in _SOURCE_GLOBS:
        for path in project_root.glob(pattern):
            if path.is_file() and path.name not in _ALLOWED_FILES:
                yield path


def test_no_hardcoded_project_id_in_source(project_root):
    """Source, config templates and build files must not embed a literal project id."""
    offenders = []
    for path in _iter_source_files(project_root):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for match in _PROJECT_LITERAL.finditer(line):
                offenders.append(f"{path.relative_to(project_root)}:{lineno}: {match.group('value')}")

    assert not offenders, (
        "Hardcoded GCP project identifiers found (must be resolved via app.config):\n"
        + "\n".join(offenders)
    )


def test_tools_yaml_is_environment_parameterized(project_root):
    """The declarative MCP contract must stay environment agnostic."""
    content = (project_root / "tools.yaml").read_text()
    assert "${PROJECT_ID}" in content
    assert "${BIGTABLE_INSTANCE_ID}" in content
    assert "${BIGTABLE_TABLE_ID}" in content


def test_env_example_contains_no_real_project(project_root):
    """`.env.example` ships placeholders only."""
    content = (project_root / ".env.example").read_text()
    assert "PROJECT_ID=<PROJECT_ID>" in content
    assert "BIGTABLE_MCP_URL=" in content


def test_project_id_resolution_prefers_environment(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "unit-test-project-001")
    config.reset_cache()
    try:
        assert config.get_project_id() == "unit-test-project-001"
    finally:
        monkeypatch.delenv("PROJECT_ID", raising=False)
        config.reset_cache()


def test_data_agent_name_is_composed_from_resolved_project(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "unit-test-project-001")
    monkeypatch.delenv("DATA_AGENT_NAME", raising=False)
    monkeypatch.setenv("DATA_AGENT_ID", "my-agent")
    config.reset_cache()
    try:
        assert config.get_data_agent_name() == (
            "projects/unit-test-project-001/locations/global/dataAgents/my-agent"
        )
    finally:
        monkeypatch.delenv("PROJECT_ID", raising=False)
        monkeypatch.delenv("DATA_AGENT_ID", raising=False)
        config.reset_cache()


def test_chunk_embeddings_table_is_fully_qualified(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "unit-test-project-001")
    monkeypatch.delenv("BQ_CHUNK_EMBEDDINGS_TABLE", raising=False)
    config.reset_cache()
    try:
        table = config.get_chunk_embeddings_table()
        assert table.startswith("unit-test-project-001.")
        assert table.endswith(".pos_manual_chunk_embeddings")
    finally:
        monkeypatch.delenv("PROJECT_ID", raising=False)
        config.reset_cache()


def test_missing_project_raises_actionable_error(monkeypatch):
    for var in ("PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GCP_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "google.auth.default", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no adc"))
    )
    config.reset_cache()
    try:
        with pytest.raises(config.ConfigurationError) as excinfo:
            config.get_project_id()
        assert "PROJECT_ID" in str(excinfo.value)
    finally:
        config.reset_cache()
