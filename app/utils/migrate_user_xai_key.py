"""
Idempotent migration: users.encrypted_xai_api_key + users.xai_model.
"""

from sqlalchemy import text, inspect
import logging

logger = logging.getLogger(__name__)


def migrate_user_xai_key(app):
    from app import db

    try:
        inspector = inspect(db.engine)
        if 'users' not in inspector.get_table_names():
            return

        columns = {col['name'] for col in inspector.get_columns('users')}
        dialect = db.engine.dialect.name

        def _add(sql: str, name: str):
            if name in columns:
                return
            logger.info('Adding %s to users...', name)
            db.session.execute(text(f'ALTER TABLE users ADD COLUMN {sql}'))
            db.session.commit()
            columns.add(name)

        if dialect == 'sqlite':
            _add('encrypted_xai_api_key BLOB', 'encrypted_xai_api_key')
            _add('xai_model VARCHAR(64)', 'xai_model')
        else:
            _add('encrypted_xai_api_key BYTEA', 'encrypted_xai_api_key')
            _add('xai_model VARCHAR(64)', 'xai_model')

    except Exception as e:
        db.session.rollback()
        logger.warning('user xai key migration skipped: %s', e)
