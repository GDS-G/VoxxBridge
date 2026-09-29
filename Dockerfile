# syntax=docker/dockerfile:1.7

# This is the hosted private-alpha image. It keeps VoxBridge on container
# loopback and reaches it through OpenAI's outbound-only Secure MCP Tunnel.
ARG TUNNEL_CLIENT_IMAGE=ghcr.io/openai/tunnel-client:v0.0.15@sha256:119799b778ba8411a124f53588f9dc837fd62ba03e5fadf77d675123c92ab58e
ARG PYTHON_IMAGE=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

FROM ${TUNNEL_CLIENT_IMAGE} AS tunnel-client
FROM ${PYTHON_IMAGE}

LABEL org.opencontainers.image.title="VoxBridge Gateway with Secure MCP Tunnel" \
      org.opencontainers.image.description="Private VoxBridge MCP gateway connected through OpenAI Secure MCP Tunnel" \
      org.opencontainers.image.source="https://github.com/GDS-G/VoxxBridge"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VOXBRIDGE_TRANSPORT=streamable-http \
    VOXBRIDGE_HOST=127.0.0.1 \
    VOXBRIDGE_PORT=8000 \
    MCP_SERVER_URL=http://127.0.0.1:8000/mcp \
    MCP_STARTUP_WAIT_TIMEOUT=60s \
    LOG_LEVEL=info \
    LOG_FORMAT=json \
    HEALTH_LISTEN_ADDR=127.0.0.1:8080

WORKDIR /app

COPY --from=tunnel-client /usr/bin/tunnel-client /usr/local/bin/tunnel-client
COPY third_party/openai-tunnel-client/LICENSE \
     third_party/openai-tunnel-client/NOTICE \
     /usr/share/doc/openai-tunnel-client/
COPY voxbridge-gateway/pyproject.toml voxbridge-gateway/README.md ./
COPY voxbridge-gateway/src ./src

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 voxbridge

USER voxbridge

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)" || exit 1

ENTRYPOINT ["voxbridge-hosted"]
