FROM python:3.13-slim AS app
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin mfp
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[web]'
ENV XDG_CONFIG_HOME=/config MFP_MCP_DATA_DIR=/data PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["mfp-mcp"]
CMD ["--host", "0.0.0.0", "--port", "8484", "web"]

FROM mcr.microsoft.com/playwright/python:v1.58.0-noble AS browser
USER root
RUN apt-get update && apt-get install -y --no-install-recommends x11vnc novnc websockify tini x11-utils python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin mfp
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY deploy/browser-entrypoint.sh /usr/local/bin/browser-entrypoint
RUN python -m venv /opt/mfp-venv && /opt/mfp-venv/bin/pip install --no-cache-dir '.[web,autorefresh]' \
    && chmod 755 /usr/local/bin/browser-entrypoint
ENV DISPLAY=:99 PYTHONUNBUFFERED=1 PATH="/opt/mfp-venv/bin:$PATH"
USER 10001:10001
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/browser-entrypoint"]
