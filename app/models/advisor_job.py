"""
Async advisor jobs — durable across gunicorn workers via the DB.

A chat turn is enqueued, HTTP returns immediately, background work updates
this row; the browser polls until status is done/error.
"""

from __future__ import annotations

from datetime import datetime
import json
import uuid

from app import db


class AdvisorJob(db.Model):
    __tablename__ = 'advisor_jobs'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    # queued | running | done | error
    status = db.Column(db.String(16), nullable=False, default='queued', index=True)
    phase = db.Column(db.String(64), nullable=True)

    # Request snapshot (JSON text)
    messages_json = db.Column(db.Text, nullable=False, default='[]')
    plan_json = db.Column(db.Text, nullable=True)
    force_grok = db.Column(db.Boolean, default=False)

    # Result / error
    result_json = db.Column(db.Text, nullable=True)
    error = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    def set_messages(self, messages) -> None:
        self.messages_json = json.dumps(messages or [], default=str)

    def get_messages(self) -> list:
        try:
            return json.loads(self.messages_json or '[]')
        except Exception:
            return []

    def set_plan(self, plan) -> None:
        if plan is None:
            self.plan_json = None
        else:
            self.plan_json = json.dumps(plan, default=str)

    def get_plan(self):
        if not self.plan_json:
            return None
        try:
            return json.loads(self.plan_json)
        except Exception:
            return None

    def set_result(self, result: dict) -> None:
        self.result_json = json.dumps(result or {}, default=str)

    def get_result(self) -> dict:
        if not self.result_json:
            return {}
        try:
            return json.loads(self.result_json)
        except Exception:
            return {}

    def to_public_dict(self, *, include_result: bool = True) -> dict:
        d = {
            'job_id': self.id,
            'status': self.status,
            'phase': self.phase,
            'created_at': self.created_at.isoformat() + 'Z' if self.created_at else None,
            'started_at': self.started_at.isoformat() + 'Z' if self.started_at else None,
            'finished_at': self.finished_at.isoformat() + 'Z' if self.finished_at else None,
            'error': self.error,
            'async': True,
        }
        if include_result and self.status == 'done':
            d['result'] = self.get_result()
        return d
