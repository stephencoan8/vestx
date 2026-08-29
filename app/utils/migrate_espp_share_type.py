"""Flip ESPP rows that were stored as RSU: grants.share_type and tax_lots.kind."""

from __future__ import annotations

import logging

from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def migrate_espp_share_type(app):
    from app import db

    try:
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names() or [])
        if 'grants' not in tables:
            return
        cols = {c['name'] for c in inspector.get_columns('grants')}
        if 'share_type' not in cols or 'grant_type' not in cols:
            return
        g = db.session.execute(text(
            "UPDATE grants SET share_type = 'espp' "
            "WHERE lower(grant_type) IN ('espp', 'nqespp') "
            "AND lower(coalesce(share_type, '')) <> 'espp'"
        ))
        n_grants = g.rowcount if g.rowcount is not None else 0

        n_lots = 0
        if 'tax_lots' in tables:
            lot_cols = {c['name'] for c in inspector.get_columns('tax_lots')}
            if 'kind' in lot_cols:
                # Lots keyed by grant
                if 'grant_id' in lot_cols:
                    r = db.session.execute(text(
                        "UPDATE tax_lots SET kind = 'espp' "
                        "WHERE kind = 'rsu' AND grant_id IN ("
                        "  SELECT id FROM grants WHERE lower(grant_type) IN ('espp', 'nqespp')"
                        ")"
                    ))
                    n_lots += r.rowcount if r.rowcount is not None else 0
                # Lots keyed only by vest (join grants)
                if 'vest_event_id' in lot_cols and 'vest_events' in tables:
                    r = db.session.execute(text(
                        "UPDATE tax_lots SET kind = 'espp' "
                        "WHERE kind = 'rsu' AND vest_event_id IN ("
                        "  SELECT ve.id FROM vest_events ve "
                        "  JOIN grants g ON g.id = ve.grant_id "
                        "  WHERE lower(g.grant_type) IN ('espp', 'nqespp')"
                        ")"
                    ))
                    n_lots += r.rowcount if r.rowcount is not None else 0

        db.session.commit()
        if n_grants or n_lots:
            logger.info(
                'ESPP migration: %s grant(s) share_type=espp, %s lot(s) kind=espp',
                n_grants, n_lots,
            )
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('espp share_type migration skipped: %s', e)
