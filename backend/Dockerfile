# Backend image — serves the API, runs the ingestion worker, and runs the grounding seed.
# One image, three roles (chosen by the compose `command`): api / worker / seed.
FROM python:3.14-slim

# uv is the package manager (never pip). Copy the static binary from the official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# git: the grounding seed shallow-clones the openly-licensed corpora at runtime.
# psycopg[binary] and curl-cffi bundle their own native libs, so no libpq/openssl-dev needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first (cached across code changes) from the lockfile only.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the app.
COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Default role: the API. On Linux the default event loop is fine, so no --reload needed
# (the Windows --reload requirement is a dev-only workaround). Overridden for worker/seed.
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
