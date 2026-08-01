"""
Add vest_events.fmv_at_vest and backfill from price history.

Idempotent; safe on Postgres (Railway) and SQLite (local).
"""

from __future__ import annotations

import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def migrate_vest_fmv(app):
    """Ensure fmv_at_vest column exists, then backfill missing snapshots."""
    from app import db

    try:
        inspector = inspect(db.engine)
        if 'vest_events' not in inspector.get_table_names():
            return

        columns = {col['name'] for col in inspector.get_columns('vest_events')}
        if 'fmv_at_vest' not in columns:
            logger.info('Adding vest_events.fmv_at_vest column…')
            db.session.execute(text(
                'ALTER TABLE vest_events ADD COLUMN fmv_at_vest FLOAT'
            ))
            db.session.commit()
            logger.info('vest_events.fmv_at_vest column added')
        else:
            logger.debug('vest_events.fmv_at_vest already exists')
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('vest_fmv column migration skipped: %s', e)
        return

    # Best-effort backfill for all users (background-safe master-key decrypt)
    try:
        from app.models.user import User
        from app.utils.vest_basis import backfill_user_vest_fmv

        users = User.query.with_entities(User.id).all()
        total_filled = 0
        total_missing = 0
        for (uid,) in users:
            try:
                stats = backfill_user_vest_fmv(uid)
                total_filled += int(stats.get('filled') or 0)
                total_missing += int(stats.get('still_missing') or 0)
            except Exception as e:
                logger.warning('vest FMV backfill failed for user %s: %s', uid, e)
        if total_filled or total_missing:
            logger.info(
                'vest FMV backfill: filled=%s still_missing=%s users=%s',
                total_filled, total_missing, len(users),
            )
    except Exception as e:
        logger.warning('vest FMV backfill skipped: %s', e)
