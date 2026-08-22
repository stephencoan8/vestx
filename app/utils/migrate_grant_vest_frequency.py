"""
Add grants.vest_frequency and resync multi-year RSU schedules to 48-month delivery.

Idempotent; safe on Postgres (Railway) and SQLite (local).
"""

from __future__ import annotations

import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def migrate_grant_vest_frequency(app):
    """Ensure vest_frequency column exists; resync RSU schedules that used 60mo/10 events."""
    from app import db

    column_added = False
    try:
        inspector = inspect(db.engine)
        if 'grants' not in inspector.get_table_names():
            return

        columns = {col['name'] for col in inspector.get_columns('grants')}
        if 'vest_frequency' not in columns:
            logger.info('Adding grants.vest_frequency column…')
            db.session.execute(text(
                "ALTER TABLE grants ADD COLUMN vest_frequency VARCHAR(20) DEFAULT 'semiannual'"
            ))
            db.session.commit()
            column_added = True
            logger.info('grants.vest_frequency column added')
        else:
            logger.debug('grants.vest_frequency already exists')
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('grant vest_frequency column migration skipped: %s', e)
        return

    # Backfill nulls
    try:
        db.session.execute(text(
            "UPDATE grants SET vest_frequency = 'semiannual' "
            "WHERE vest_frequency IS NULL OR vest_frequency = ''"
        ))
        db.session.commit()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('grant vest_frequency backfill skipped: %s', e)

    # Resync multi-year RSU/RSA schedules so 60mo/10-event LTI becomes 48mo/8-event.
    # Safe sync preserves tax/sale-linked vest rows.
    try:
        from app.models.grant import Grant, ShareType
        from app.utils.vest_calculator import calculate_vest_schedule
        from app.utils.sync_vest_schedule import sync_vest_events_for_grant

        grants = Grant.query.filter(
            Grant.share_type.in_([ShareType.RSU.value, 'rsa']),
            Grant.vest_years >= 5,
        ).all()
        total = {'created': 0, 'updated': 0, 'deleted': 0, 'preserved': 0, 'grants': 0}
        for grant in grants:
            try:
                if not grant.vest_frequency:
                    grant.vest_frequency = 'semiannual'
                schedule = calculate_vest_schedule(grant)
                stats = sync_vest_events_for_grant(grant, schedule)
                for k in ('created', 'updated', 'deleted', 'preserved'):
                    total[k] += int(stats.get(k) or 0)
                total['grants'] += 1
            except Exception as e:
                logger.warning('schedule resync failed for grant %s: %s', grant.id, e)
        db.session.commit()
        logger.info(
            'RSU schedule resync (48mo): grants=%s created=%s updated=%s deleted=%s preserved=%s column_added=%s',
            total['grants'], total['created'], total['updated'], total['deleted'],
            total['preserved'], column_added,
        )
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('RSU schedule resync skipped: %s', e)
