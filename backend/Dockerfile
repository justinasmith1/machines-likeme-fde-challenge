# Backend API. Two stages: the first installs the application and its dependencies into
# a virtual environment; the second is the image that runs, with that environment and
# nothing else. No .env, no data, no tests: see .dockerignore, and infra/03-build-push.sh
# checks the result before pushing.

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /src
COPY pyproject.toml ./
COPY app ./app
# Installs the "app" package itself, so the final image needs no source tree.
RUN pip install .


FROM python:3.12-slim
# Every path is absolute and under /app/data, where the deployment mounts the file share.
# Nothing is read from a .env file: configuration and secrets come from the environment.
# APP_ENV is deliberately not set here: without it the API refuses every request.
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL="sqlite:////app/data/contoso.db" \
    UPLOADS_DIR="/app/data/uploads" \
    EXTRACTION_CACHE_DIR="/app/data/cache/extractions" \
    REFERENCE_DATA_DIR="/app/data/reference"
RUN useradd --system --uid 10001 --create-home appuser \
    && mkdir -p /app/data \
    && chown appuser /app/data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
USER appuser
EXPOSE 8000
# One process: the database is SQLite and extraction runs in this process's background tasks.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
