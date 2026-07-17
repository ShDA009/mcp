FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src/ ./src/

RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

ENTRYPOINT ["uv", "run", "--no-sync", "zephyr-mcp"]
