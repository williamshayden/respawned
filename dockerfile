FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ src/

RUN uv sync --frozen --no-dev

CMD ["uv", "run", "--no-sync", "python", "-m", "respawned"]
