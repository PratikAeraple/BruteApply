FROM python:3.12.16-slim-trixie@sha256:8523d6abeb48ceae0b7240d30c5808fc26cb79c1a635bacdf19cf3baecfbec07 AS dependencies
WORKDIR /build
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --prefix=/install --require-hashes -r requirements.lock

FROM python:3.12.16-slim-trixie@sha256:8523d6abeb48ceae0b7240d30c5808fc26cb79c1a635bacdf19cf3baecfbec07 AS runtime
ENV PATH=/install/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/install/lib/python3.12/site-packages
WORKDIR /app
RUN groupadd --system --gid 10001 bruteapply && useradd --system --uid 10001 --gid bruteapply --home-dir /nonexistent --shell /usr/sbin/nologin bruteapply
COPY --from=dependencies /install /install
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 migrations ./migrations
COPY --chown=10001:10001 scripts ./scripts
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
