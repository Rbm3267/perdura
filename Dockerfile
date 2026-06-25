# syntax=docker/dockerfile:1
#
# Builds perdura_service.py (enterprise track E1/E2 HTTP API). The CLI
# (perdura.py) and MCP station (perdura_server.py) run fine from this same
# image by overriding `command:` -- nothing here is service-specific except
# the default ENTRYPOINT/CMD.

FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml README.md ./
COPY *.py ./
RUN pip install --no-cache-dir --prefix=/install ".[enterprise]"

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY *.py ./
RUN useradd --create-home --uid 1000 perdura && chown -R perdura:perdura /app
USER perdura

EXPOSE 8900
ENTRYPOINT ["python", "perdura_service.py"]
CMD ["--host", "0.0.0.0", "--port", "8900"]
