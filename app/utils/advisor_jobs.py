"""
Enqueue / poll advisor jobs without blocking the HTTP worker for the full turn.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from flask import current_app

from app import db
from app.models.advisor_job import AdvisorJob

logger = logging.getLogger(__name__)


def _purge_old_jobs(user_id: int, keep_hours: int = 24) -> None:
    try:
        cutoff = datetime.utcnow() - timedelta(hours=keep_hours)
        (
            AdvisorJob.query
            .filter(AdvisorJob.user_id == user_id, AdvisorJob.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.session.commit()
    except Exception as e:
        logger.warning('purge advisor jobs failed: %s', e)
        db.session.rollback()


def enqueue_advisor_job(
    *,
    user_id: int,
    messages: List[dict],
    plan: Optional[dict] = None,
    force_grok: bool = False,
) -> AdvisorJob:
    """
    Create a job row and start a daemon thread. Returns immediately with status=queued.
    """
    _purge_old_jobs(user_id)

    job = AdvisorJob(
        user_id=user_id,
        status='queued',
        phase='queued',
        force_grok=bool(force_grok),
    )
    job.set_messages(messages)
    job.set_plan(plan)
    db.session.add(job)
    db.session.commit()

    app = current_app._get_current_object()
    job_id = job.id

    t = threading.Thread(
        target=_thread_entry,
        args=(app, job_id),
        name=f'advisor-job-{job_id[:8]}',
        daemon=True,
    )
    t.start()
    logger.info('advisor job enqueued id=%s user=%s', job_id, user_id)
    return job


def _thread_entry(app, job_id: str) -> None:
    from app.utils.advisor_service import execute_job_in_background
    try:
        execute_job_in_background(app, job_id)
    except Exception:
        logger.exception('advisor thread died for %s', job_id)


def get_job_for_user(job_id: str, user_id: int) -> Optional[AdvisorJob]:
    if not job_id:
        return None
    job = AdvisorJob.query.get(job_id)
    if not job or job.user_id != user_id:
        return None
    return job


def job_public_payload(job: AdvisorJob) -> Dict[str, Any]:
    return job.to_public_dict(include_result=True)
