"""
Idempotent migration: advisor_jobs table for async chat.
Never raise — boot must survive even if this fails (chat falls back).
"""

from __future__ import annotations

import logging

from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def migrate_advisor_jobs(app) -> None:
    from app import db

    try:
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names() or [])
        if 'advisor_jobs' in tables:
            logger.debug('advisor_jobs already exists')
            return

        dialect = db.engine.dialect.name
        logger.info('Creating advisor_jobs table (dialect=%s)', dialect)

        if dialect == 'sqlite':
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS advisor_jobs (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'queued',
                    phase VARCHAR(64),
                    messages_json TEXT NOT NULL DEFAULT '[]',
                    plan_json TEXT,
                    force_grok BOOLEAN DEFAULT 0,
                    result_json TEXT,
                    error TEXT,
                    created_at DATETIME NOT NULL,
                    started_at DATETIME,
                    finished_at DATETIME
                )
            """))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_advisor_jobs_user_id ON advisor_jobs (user_id)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_advisor_jobs_status ON advisor_jobs (status)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_advisor_jobs_created_at ON advisor_jobs (created_at)"
            ))
        else:
            # Postgres / others — match SQLAlchemy model (FK optional if users missing)
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS advisor_jobs (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'queued',
                    phase VARCHAR(64),
                    messages_json TEXT NOT NULL DEFAULT '[]',
                    plan_json TEXT,
                    force_grok BOOLEAN DEFAULT FALSE,
                    result_json TEXT,
                    error TEXT,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
                    started_at TIMESTAMP WITHOUT TIME ZONE,
                    finished_at TIMESTAMP WITHOUT TIME ZONE
                )
            """))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_advisor_jobs_user_id ON advisor_jobs (user_id)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_advisor_jobs_status ON advisor_jobs (status)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_advisor_jobs_created_at ON advisor_jobs (created_at)"
            ))

        db.session.commit()
        logger.info('advisor_jobs table ready')
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('advisor_jobs migration skipped: %s', e)
