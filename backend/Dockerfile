FROM python:3.12-slim

ARG UV_VERSION=0.13.5

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /workspace/backend
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml uv.lock ./
COPY app ./app
RUN uv sync --frozen --no-dev

COPY tests ./tests
