# =============================================================================
# Makefile - ADK Cymbal Operations Coordinator Agent
#
# Every target is environment agnostic. Values come from `.env` (see
# `.env.example`) or from your active gcloud / ADC configuration - no project id
# is baked into any recipe.
# =============================================================================

SHELL := /bin/bash
VENV := .venv
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
ADK := $(VENV)/bin/adk

# Resolved lazily so `make help` works without gcloud installed.
PROJECT_ID ?= $(shell . ./.env 2>/dev/null; echo $${PROJECT_ID:-$$(gcloud config get-value project 2>/dev/null)})
REGION ?= $(shell . ./.env 2>/dev/null; echo $${REGION:-us-central1})
IMAGE ?= $(REGION)-docker.pkg.dev/$(PROJECT_ID)/cymbal-agents/cymbal-operations-agent:latest
AGENT_SERVICE ?= cymbal-operations-agent

.PHONY: help env setup install test test-unit test-integration test-e2e run-web \
        docker-build docker-run mcp-render mcp-deploy mcp-verify deploy-agent clean

help:
	@echo "Environment"
	@echo "  env             - Show the resolved deployment environment"
	@echo "  setup           - Create the virtual environment (uv, Python 3.11)"
	@echo "  install         - Install runtime + test dependencies"
	@echo ""
	@echo "Quality gates"
	@echo "  test            - Full pytest suite (unit + integration)"
	@echo "  test-unit       - Offline tests only (no GCP calls, CI safe)"
	@echo "  test-integration- Live GCP contract tests only"
	@echo "  test-e2e        - 7 operational use cases against the live agent"
	@echo ""
	@echo "MCP microservice (declarative tools.yaml contract)"
	@echo "  mcp-render      - Render tools.yaml for the active environment"
	@echo "  mcp-deploy      - Render + push to Secret Manager + deploy Cloud Run"
	@echo "  mcp-verify      - Assert the live MCP endpoint serves the declared tools"
	@echo ""
	@echo "Run & ship"
	@echo "  run-web         - Start the ADK Web UI locally"
	@echo "  docker-build    - Build the agent container image"
	@echo "  docker-run      - Run the container locally with mounted ADC"
	@echo "  deploy-agent    - Deploy the agent container to Cloud Run"
	@echo "  clean           - Remove caches and rendered build artifacts"

env:
	@echo "PROJECT_ID = $(PROJECT_ID)"
	@echo "REGION     = $(REGION)"
	@echo "IMAGE      = $(IMAGE)"

setup:
	uv venv --python 3.11 $(VENV)
	@echo "Virtual environment created at $(VENV)"

install:
	$(VENV)/bin/uv pip install -r requirements.txt
	$(VENV)/bin/uv pip install pytest pytest-asyncio pyyaml

test:
	PYTHONPATH=. $(PYTEST)

test-unit:
	PYTHONPATH=. $(PYTEST) -m "not integration"

test-integration:
	PYTHONPATH=. $(PYTEST) -m integration

test-e2e:
	PYTHONPATH=. $(PYTHON) run_all_tests.py

run-web:
	$(ADK) web app --host 0.0.0.0 --port 8080

mcp-render:
	./scripts/deploy_mcp.sh --render

mcp-deploy:
	./scripts/deploy_mcp.sh

mcp-verify:
	./scripts/deploy_mcp.sh --verify

docker-build:
	docker build -t $(IMAGE) .

docker-run:
	docker run --rm -p 8080:8080 \
	  -e PROJECT_ID=$(PROJECT_ID) \
	  -e GOOGLE_CLOUD_PROJECT=$(PROJECT_ID) \
	  --env-file .env \
	  -v $$HOME/.config/gcloud:/root/.config/gcloud:ro \
	  $(IMAGE)

deploy-agent: docker-build
	docker push $(IMAGE)
	gcloud run deploy $(AGENT_SERVICE) \
	  --project=$(PROJECT_ID) --region=$(REGION) \
	  --image=$(IMAGE) --port=8080 --no-allow-unauthenticated \
	  --set-env-vars=PROJECT_ID=$(PROJECT_ID),GOOGLE_CLOUD_PROJECT=$(PROJECT_ID)

clean:
	rm -rf .pytest_cache build __pycache__ app/__pycache__ app/tools/__pycache__ tests/__pycache__
