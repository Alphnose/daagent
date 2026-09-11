# =============================================================================
# Production container - Cymbal Superstores Operations Coordinator Agent
#
# Serves the ADK FastAPI surface (adk_api + A2A + reasoning_engine routes), which
# is what Vertex AI Agent Runtime and the Cloud Console Playground call.
#
# The image carries NO environment-specific identifier. PROJECT_ID and every
# other setting are injected at runtime (Agent Runtime deployment_spec.env,
# Cloud Run --set-env-vars, or `docker run --env-file .env`).
# =============================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

# Dependency layer. `uv sync --frozen` refuses to re-resolve, so the image is
# reproducible and inherits the public-PyPI index pinned in pyproject.toml.
COPY ./pyproject.toml ./README.md ./uv.lock* ./
COPY ./app ./app
RUN uv sync --frozen --no-dev

# Declarative MCP contract and quality gates travel with the image so the same
# artifact can be verified in any environment.
COPY ./tools.yaml ./pytest.ini ./run_all_tests.py ./
COPY ./tests ./tests
COPY ./scripts ./scripts

ARG AGENT_VERSION=1.0.0
ENV AGENT_VERSION=${AGENT_VERSION}

EXPOSE 8080

CMD ["sh", "-c", "uv run uvicorn app.fast_api_app:app --host 0.0.0.0 --port ${PORT}"]
