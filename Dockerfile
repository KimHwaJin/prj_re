ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE} AS dependencies

ARG UV_VERSION=0.11.29
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock ./
# Use the repository lock, without installing the legacy root-module layout.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM ${BASE_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --create-home app

COPY --from=dependencies /app/.venv /app/.venv
COPY pyproject.toml uv.lock README.md cli.py run.py app.py config*.yml ./
COPY config.example.yml ./config.yml
COPY src ./src
COPY migrations ./migrations
COPY crud_migrations ./crud_migrations
COPY alembic.ini alembic.crud.ini ./
COPY mock_data ./mock_data
COPY scripts/local ./scripts/local
COPY scripts/migrate.py scripts/configure.py ./scripts/

RUN mkdir -p /workspace/pv/data /app/var/workflows /app/workspace && \
    cp /app/mock_data/*.parquet /workspace/pv/data/ && \
    chown -R app:app /app/var /app/workspace /workspace/pv

USER 10001:10001

ARG SOURCE_REVISION=working-tree
ENV SOURCE_REVISION=${SOURCE_REVISION}
LABEL org.opencontainers.image.revision=${SOURCE_REVISION}

EXPOSE 8000

CMD ["python", "app.py"]
