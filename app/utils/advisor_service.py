"""
Advisor pipeline (deterministic engines + optional Grok).

Used by:
  - background job worker (async chat — never blocks page loads)
  - optional sync endpoint fallback
"""

from __future__ import annotations

import logging
import traceback
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def slim_engine_plan(payload: Optional[dict]) -> Optional[dict]:
    """Keep UI-sync fields; drop bulky tax_analysis for JSON transport."""
    if not payload or not isinstance(payload, dict):
        return payload
    out = dict(payload)
    out.pop('tax_analysis', None)
    alts = out.get('alternatives')
    if isinstance(alts, list):
        slim_alts = []
        for a in alts[:5]:
            if not isinstance(a, dict):
                continue
            aa = dict(a)
            aa.pop('tax_analysis', None)
            slim_alts.append(aa)
        out['alternatives'] = slim_alts
    return out


def run_advisor_turn(
    *,
    user_id: int,
    messages: List[dict],
    plan: Optional[dict] = None,
    force_grok: bool = False,
) -> Dict[str, Any]:
    """
    Full advisor turn. Safe to call from a background thread with app context.

    Returns a JSON-serializable dict matching the chat API shape
    (success, reply, engine_plan, context_meta, …).
    """
    from flask import url_for
    from app.models.tax_profile import TaxProfile
    from app.models.user import User
    from app.utils.lot_inventory import build_lots_for_user
    from app.utils.price_utils import get_latest_user_price
    from app.utils import xai_advisor
    from app.utils.advisor_router import route_and_compute

    user = User.query.get(user_id)
    if not user:
        return {
            'success': False,
            'error': 'User not found',
            'code': 'no_user',
            'phase': 'validate',
            'api_ok': False,
        }

    messages = messages or []
    if not messages:
        return {
            'success': False,
            'error': 'messages required',
            'code': 'bad_request',
            'phase': 'validate',
            'api_ok': False,
        }

    last_user = ''
    for m in reversed(messages):
        if (m.get('role') or '') == 'user':
            last_user = m.get('content') or ''
            break

    eng = {
        'filing_status': 'single',
        'state_code': 'CA',
        'tax_year': date.today().year,
        'other_ordinary_income': 0.0,
        'use_bracket_engine': True,
        'use_state_engine': True,
        'include_niit': True,
        'include_fica': False,
        'ytd_wages': 0.0,
        'ss_wage_base_maxed': False,
        'amt_credit_carryforward': 0.0,
        'ca_amt_credit_carryforward': 0.0,
        'other_long_term_gains': 0.0,
        'other_short_term_gains': 0.0,
    }
    try:
        profile = TaxProfile.for_user(user)
        eng = profile.to_engine_dict()
    except Exception as e:
        logger.warning('tax profile load failed: %s', e)

    lots = []
    try:
        lots = build_lots_for_user(user_id) or []
    except Exception as e:
        logger.warning('lots load failed: %s', e)

    live = 0.0
    try:
        live = float(get_latest_user_price(user_id) or 0.0)
    except Exception:
        live = 0.0

    if plan is not None and not isinstance(plan, dict):
        plan = None

    try:
        routed = route_and_compute(
            user_message=last_user,
            profile_dict=eng,
            inventory_lots=lots,
            live_price=live,
            plan=plan,
            force_grok=bool(force_grok),
        )
    except Exception as e:
        logger.error('route_and_compute failed: %s', e, exc_info=True)
        return {
            'success': False,
            'error': f'Engine failed: {e}',
            'code': 'engine_error',
            'phase': 'engine',
            'detail': traceback.format_exc()[-1500:],
            'api_ok': False,
        }

    # Engine-only
    if routed.skip_grok and routed.deterministic_reply:
        grok_on = False
        try:
            grok_on = xai_advisor.is_configured(user)
        except Exception:
            pass
        return {
            'success': True,
            'reply': routed.deterministic_reply,
            'grok_enabled': grok_on,
            'used_grok': False,
            'api_ok': True,
            'phase': 'engine_done',
            'engine_plan': slim_engine_plan(routed.engine_payload),
            'context_meta': {
                **routed.to_meta(),
                'live_price': live,
                'lots': len(lots),
                'est_context_tokens': 0,
            },
        }

    grok_on = False
    try:
        grok_on = xai_advisor.is_configured(user)
    except Exception as e:
        logger.warning('is_configured failed: %s', e)

    if not grok_on:
        if routed.deterministic_reply:
            return {
                'success': True,
                'reply': routed.deterministic_reply + (
                    '\n\n_Add an xAI key in Settings for narrative explanations._'
                ),
                'used_grok': False,
                'api_ok': True,
                'phase': 'engine_done_no_key',
                'engine_plan': slim_engine_plan(routed.engine_payload),
                'grok_enabled': False,
                'context_meta': {
                    **routed.to_meta(),
                    'live_price': live,
                    'lots': len(lots),
                },
            }
        settings_url = '/settings/profile'
        try:
            settings_url = url_for('settings.profile')
        except Exception:
            pass
        return {
            'success': False,
            'error': (
                'Add your xAI API key under Settings for open-ended questions. '
                'Or ask a computable question like “net $500k minimize tax”.'
            ),
            'code': 'no_api_key',
            'grok_enabled': False,
            'settings_url': settings_url,
            'phase': 'need_key',
            'api_ok': True,
        }

    try:
        from app.utils.account_context import pack_context_for_prompt
        packed = pack_context_for_prompt(
            user_id,
            user_message=last_user,
            plan=plan or routed.engine_payload,
            mode='full',
        )
    except Exception as e:
        logger.error('pack_context failed: %s', e, exc_info=True)
        packed = {
            'text': f'## SNAPSHOT\npack_error={e}\nlive_price={live}\nlots={len(lots)}',
            'meta': {'est_tokens': 20, 'lot_count': len(lots), 'live_price': live},
        }

    account_blob = packed.get('text') or ''
    if routed.engine_text:
        account_blob = (
            '## ENGINE_RESULT (authoritative $ and picks — do not invent alternatives)\n'
            + routed.engine_text
            + '\n\n'
            + account_blob
        )

    try:
        reply = xai_advisor.advisor_chat(
            messages=messages,
            plan=plan or routed.engine_payload,
            user=user,
            account_context=account_blob,
        )
    except Exception as e:
        logger.error('Grok API call failed: %s', e, exc_info=True)
        if routed.deterministic_reply:
            return {
                'success': True,
                'reply': routed.deterministic_reply + f'\n\n_Grok API error: {e}_',
                'used_grok': False,
                'api_ok': False,
                'grok_error': str(e),
                'phase': 'grok_failed_engine_fallback',
                'engine_plan': slim_engine_plan(routed.engine_payload),
                'grok_enabled': True,
                'context_meta': {
                    **routed.to_meta(),
                    'live_price': live,
                    'lots': len(lots),
                },
            }
        return {
            'success': False,
            'error': f'Grok API call failed: {e}',
            'code': 'grok_api_error',
            'phase': 'grok_failed',
            'grok_enabled': True,
            'api_ok': False,
            'detail': traceback.format_exc()[-1200:],
        }

    if routed.deterministic_reply and routed.mode == 'engine_then_grok':
        reply = (
            routed.deterministic_reply
            + '\n\n---\n**Explanation**\n'
            + reply
        )

    meta = packed.get('meta') or {}
    return {
        'success': True,
        'reply': reply or '(empty model reply)',
        'grok_enabled': True,
        'used_grok': True,
        'api_ok': True,
        'phase': 'grok_done',
        'engine_plan': slim_engine_plan(routed.engine_payload),
        'context_meta': {
            'lots': meta.get('lot_count') or len(lots),
            'live_price': meta.get('live_price') or live,
            'as_of': meta.get('as_of'),
            'est_context_tokens': meta.get('est_tokens'),
            'context_chars': meta.get('chars'),
            'tier': meta.get('tier'),
            **routed.to_meta(),
        },
    }


def execute_job_in_background(app, job_id: str) -> None:
    """Daemon-thread entry: run one job, write result to DB. Never touches the request cycle."""
    from datetime import datetime
    from app import db
    from app.models.advisor_job import AdvisorJob

    with app.app_context():
        job = AdvisorJob.query.get(job_id)
        if not job:
            logger.error('advisor job missing: %s', job_id)
            return
        try:
            job.status = 'running'
            job.phase = 'engines'
            job.started_at = datetime.utcnow()
            db.session.commit()

            result = run_advisor_turn(
                user_id=job.user_id,
                messages=job.get_messages(),
                plan=job.get_plan(),
                force_grok=bool(job.force_grok),
            )

            job.phase = (result or {}).get('phase') or 'done'
            if result.get('success') is False and not result.get('reply'):
                job.status = 'error'
                job.error = result.get('error') or 'Advisor failed'
                job.set_result(result)
            else:
                # Even partial/engine fallback with reply counts as done for UI
                job.status = 'done'
                job.error = None
                job.set_result(result)
            job.finished_at = datetime.utcnow()
            db.session.commit()
        except Exception as e:
            logger.error('advisor job %s crashed: %s', job_id, e, exc_info=True)
            try:
                db.session.rollback()
                job = AdvisorJob.query.get(job_id)
                if job:
                    job.status = 'error'
                    job.error = str(e)
                    job.phase = 'job_crash'
                    job.set_result({
                        'success': False,
                        'error': str(e),
                        'phase': 'job_crash',
                        'api_ok': False,
                        'detail': traceback.format_exc()[-1200:],
                    })
                    job.finished_at = datetime.utcnow()
                    db.session.commit()
            except Exception:
                logger.exception('failed to persist job error for %s', job_id)
