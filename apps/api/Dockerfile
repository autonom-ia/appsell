FROM ghcr.io/astral-sh/uv:0.11.13@sha256:841c8e6fe30a8b07b4478d12d0c608cba6de66102d29d65d1cc423af86051563 AS uv

FROM python:3.12.11-slim@sha256:47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /opt/app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12.11-slim@sha256:47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f AS runtime
ENV PATH="/opt/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN groupadd --system appsell && useradd --system --gid appsell --home /opt/app appsell
WORKDIR /opt/app
COPY --from=builder /opt/app/.venv .venv
COPY src src
COPY pyproject.toml ./
RUN chown -R appsell:appsell /opt/app
USER appsell
EXPOSE 8000
CMD ["uvicorn", "appsell_api.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
