from __future__ import annotations

import os
from celery import Celery

broker = os.getenv("REDIS_URL", "redis://redis:6379/0")
celery_app = Celery("bruteapply", broker=broker, backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1"))
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_acks_on_failure_or_timeout=False,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_ignore_result=True,
    broker_transport_options={"visibility_timeout": 3600},
    result_expires=3600,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Toronto",
    enable_utc=True,
)


@celery_app.task(name="bruteapply.discovery.run", bind=True, max_retries=5, autoretry_for=(RuntimeError,), retry_backoff=True, retry_jitter=True)
def run_discovery(self, source_name: str, run_key: str) -> dict:
    # Providers are registered only after terms and account access are reviewed.
    # No network scraping or external source is enabled by default.
    raise RuntimeError("no permitted discovery provider is configured")


@celery_app.task(name="bruteapply.sync.sheet", bind=True, max_retries=5, autoretry_for=(RuntimeError,), retry_backoff=True, retry_jitter=True)
def run_sheet_sync(self, sync_key: str) -> dict:
    # The live spreadsheet connector is intentionally absent from demo mode.
    raise RuntimeError("Google OAuth and first-write snapshot are required")


@celery_app.task(name="bruteapply.documents.prepare", bind=True, max_retries=3, autoretry_for=(RuntimeError,), retry_backoff=True)
def prepare_documents(self, application_id: str, idempotency_key: str) -> dict:
    raise RuntimeError("owner-verified facts and an approved document provider are required")


@celery_app.task(name="bruteapply.application.submit", bind=True, max_retries=0)
def submit_application(self, application_id: str, idempotency_key: str) -> dict:
    # A real submission adapter does not exist. This task cannot submit.
    raise RuntimeError("no permitted submission adapter; create MANUAL_SUBMISSION_REQUIRED ticket")
