"""First-class tax lots — remaining_qty is inventory SSOT."""

from __future__ import annotations

from datetime import datetime

from app import db


class TaxLot(db.Model):
    __tablename__ = 'tax_lots'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    grant_id = db.Column(db.Integer, nullable=True, index=True)
    vest_event_id = db.Column(db.Integer, nullable=True, index=True)
    parent_lot_id = db.Column(db.Integer, nullable=True, index=True)

    # rsu | iso_option | iso_stock | espp | cash
    kind = db.Column(db.String(20), nullable=False, index=True)
    acquired_date = db.Column(db.Date, nullable=False)
    original_qty = db.Column(db.Float, nullable=False)
    remaining_qty = db.Column(db.Float, nullable=False)
    cost_basis_per_share = db.Column(db.Float, nullable=False, default=0.0)
    fmv_at_open = db.Column(db.Float, nullable=True)
    strike_price = db.Column(db.Float, nullable=True)
    # open | closed
    status = db.Column(db.String(12), nullable=False, default='open', index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def is_stock(self) -> bool:
        return self.kind in ('rsu', 'iso_stock', 'espp')

    def is_option(self) -> bool:
        return self.kind == 'iso_option'

    def close_if_empty(self) -> None:
        if float(self.remaining_qty or 0) <= 0:
            self.remaining_qty = 0.0
            self.status = 'closed'


class LedgerEntry(db.Model):
    """Append-only journal. Corrections are reverse rows, never UPDATEs of qty."""

    __tablename__ = 'ledger_entries'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    lot_id = db.Column(db.Integer, nullable=False, index=True)
    # vest | withhold | exercise | sale | reverse
    kind = db.Column(db.String(16), nullable=False, index=True)
    entry_date = db.Column(db.Date, nullable=False, index=True)
    qty = db.Column(db.Float, nullable=False)  # signed: + open, − outflow
    price = db.Column(db.Float, nullable=True)
    cash = db.Column(db.Float, nullable=True)
    fees = db.Column(db.Float, nullable=True, default=0.0)
    sale_id = db.Column(db.Integer, nullable=True, index=True)
    exercise_id = db.Column(db.Integer, nullable=True, index=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
