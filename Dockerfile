# Production Dockerfile for ADK Cymbal Operations Coordinator Agent
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    GOOGLE_GENAI_USE_VERTEXAI=True \
    GOOGLE_CLOUD_LOCATION=global \
    GEMINI_MODEL=gemini-3.6-flash

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifests
COPY requirements.txt ./

# Install python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir pytest pytest-asyncio pyyaml

# Copy application source code and configuration
COPY app/ ./app/
COPY tests/ ./tests/
COPY tools.yaml ./
COPY run_all_tests.py ./
COPY Makefile ./

# Expose Web UI / API port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Default entrypoint starts the ADK Web UI
CMD ["adk", "web", "app", "--host", "0.0.0.0", "--port", "8080"]
