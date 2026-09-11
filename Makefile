# Makefile for ADK Cymbal Operations Coordinator Agent

SHELL := /bin/bash
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
ADK := $(VENV)/bin/adk

.PHONY: help setup install test test-pytest test-e2e run-web lint clean

help:
	@echo "Available targets:"
	@echo "  setup        - Create virtual environment with uv"
	@echo "  install      - Install dependencies from requirements.txt"
	@echo "  test         - Run unit and operational test suite with pytest"
	@echo "  test-e2e     - Run end-to-end 7 use case validation suite"
	@echo "  run-web      - Start ADK Web UI (adk web app)"
	@echo "  clean        - Remove temporary files and pytest cache"

setup:
	uv venv --python 3.11 $(VENV)
	@echo "Virtual environment created at $(VENV)"

install:
	$(VENV)/bin/uv pip install -r requirements.txt
	$(VENV)/bin/uv pip install pytest pytest-asyncio pyyaml

test:
	PYTHONPATH=. $(PYTEST) -v tests/test_operational_use_cases.py

test-e2e:
	PYTHONPATH=. $(PYTHON) run_all_tests.py

run-web:
	$(ADK) web app --host 0.0.0.0 --port 8080

clean:
	rm -rf .pytest_cache __pycache__ app/__pycache__ app/tools/__pycache__ tests/__pycache__
