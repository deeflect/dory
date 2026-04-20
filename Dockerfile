# Multi-stage build.
#
# Builder: install uv + project into a self-contained venv at /app/.venv.
# Runtime: copy the venv and src, run as UID 10000 with HOME on the mounted
# data volume. Container /app is read-only at runtime; all writable state
# lives in /var/lib/dory (bind-mounted from the host) or /tmp (tmpfs).

FROM python:3.12-slim AS builder
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY README.md ./
COPY src ./src

# --frozen honors uv.lock exactly; --no-dev skips the dev extras.
RUN uv sync --frozen --no-dev

# ---

FROM python:3.12-slim
WORKDIR /app

# Fixed non-root user. UID matches compose `user: "10000:10000"` so the
# bind-mounted corpus volume can be chowned once on the host and stay
# writable across container recreates.
RUN groupadd -g 10000 dory \
 && useradd -u 10000 -g 10000 -M -d /var/lib/dory -s /usr/sbin/nologin dory

COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/var/lib/dory \
    DORY_ROOT=/var/lib/dory \
    DORY_CORPUS_ROOT=/var/lib/dory \
    DORY_INDEX_ROOT=/var/lib/dory/.index \
    DORY_HTTP_HOST=0.0.0.0 \
    DORY_HTTP_PORT=8766

USER 10000:10000

EXPOSE 8765 8766

CMD ["dory-http"]
