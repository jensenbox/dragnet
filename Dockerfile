FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY . .
RUN uv sync --frozen --no-dev

FROM python:3.13-slim-bookworm
RUN useradd --create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
RUN mkdir -p /app/staticfiles && chown app:app /app/staticfiles
USER app
RUN SECRET_KEY=collectstatic-placeholder python manage.py collectstatic --noinput
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
