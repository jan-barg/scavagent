# Cloud Run image. Cloud Build builds this on every push to main (see docs/DEPLOY.md).
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first, so code changes reuse the cached layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

# Cloud Run sets PORT (8080 by default) and needs the server on 0.0.0.0.
ENV PATH="/app/.venv/bin:$PATH" HOST=0.0.0.0 PORT=8080
CMD ["python", "app.py"]
