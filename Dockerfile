FROM python:3.13-slim AS base

ARG EXTRAS=""

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin mfp

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".${EXTRAS}"

# Credentials live under XDG_CONFIG_HOME; the SQLite archive under MFP_MCP_DATA_DIR.
ENV XDG_CONFIG_HOME=/config \
    MFP_MCP_DATA_DIR=/data \
    MFP_LOG_LEVEL=WARNING \
    PYTHONUNBUFFERED=1

RUN mkdir -p /config /data && chown -R 10001:10001 /config /data && chmod 700 /config /data

USER 10001:10001
VOLUME ["/config", "/data"]
EXPOSE 8484

ENTRYPOINT ["mfp-mcp"]
CMD ["--http", "--host", "0.0.0.0", "--port", "8484", "serve"]


# Optional Playwright-backed session refresh. Build with:
#   docker build --target autorefresh -t mfp-mcp:autorefresh .
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy AS autorefresh

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin mfp

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[autorefresh]"

ENV XDG_CONFIG_HOME=/config \
    MFP_MCP_DATA_DIR=/data \
    MFP_LOG_LEVEL=WARNING \
    PYTHONUNBUFFERED=1

RUN mkdir -p /config /data && chown -R 10001:10001 /config /data && chmod 700 /config /data

USER 10001:10001
VOLUME ["/config", "/data"]
EXPOSE 8484

ENTRYPOINT ["mfp-mcp"]
CMD ["--http", "--host", "0.0.0.0", "--port", "8484", "serve"]
