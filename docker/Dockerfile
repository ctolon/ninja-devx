# Python 3.13, Node 22 and uv 0.11.6; update digests deliberately.
FROM ghcr.io/astral-sh/uv@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754 AS uv
FROM node@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS node
FROM python@sha256:e06cc1111ed84189e91866447f562b89faadbfbbb9937cd67e6bf4172cdb45df
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global typescript@5.9.3
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project --group docs --group verification
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --group docs --group verification
CMD ["python", "tools/verify_local.py", "--output", "/tmp/devx-validation", "--timeout", "300"]
