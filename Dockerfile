# Lean runtime image: HTTP MCP on PORT (default 8080)
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

RUN useradd --system --no-create-home --shell /usr/sbin/nologin appuser

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --upgrade pip \
    && pip install --no-cache-dir .

USER appuser
EXPOSE 8080

CMD ["easydeploy-ai-mcp-http"]
