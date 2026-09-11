# =============================================================================
# Production container - ADK Cymbal Operations Coordinator Agent
#
# The image carries NO environment-specific identifier. PROJECT_ID and every
# other setting are injected at runtime (`--env-file .env`, Cloud Run
# `--set-env-vars`, or the metadata server via ADC).
# =============================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/srv \
    PORT=8080 \
    GOOGLE_GENAI_USE_VERTEXAI=True \
    GOOGLE_CLOUD_LOCATION=global

WORKDIR /srv

# System dependencies (curl is used by the health check)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies (cached layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir pytest pytest-asyncio pyyaml

# Application source, declarative MCP contract and quality gates
COPY app/ ./app/
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY tools.yaml pytest.ini run_all_tests.py Makefile ./

# Run as an unprivileged user
RUN useradd --create-home --uid 1000 agent && chown -R agent:agent /srv
USER agent

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8080/ || exit 1

# ADK Web UI / API server. Cloud Run injects $PORT.
CMD exec adk web app --host 0.0.0.0 --port ${PORT}
