"""
SpaceXAI / xAI Grok advisor for equity planning.

Deterministic math stays in goal_optimizer + tax_engine.
Grok is used for:
  1) Parsing natural-language goals into GoalRequest JSON
  2) Explaining tradeoffs / nuance of a computed plan
  3) Free-form Q&A with plan + inventory context

API keys are per-user (encrypted on the User row). Optional server XAI_API_KEY
is only a fallback for single-tenant/dev and is never required for multi-user.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv('XAI_MODEL', 'grok-4.5')
BASE_URL = os.getenv('XAI_BASE_URL', 'https://api.x.ai/v1')


def _user_api_key(user) -> Optional[str]:
    if user is None:
        return None
    getter = getattr(user, 'get_xai_api_key', None)
    if not callable(getter):
        return None
    try:
        key = getter()
        return (key or '').strip() or None
    except Exception as e:
        logger.warning('Failed to load user xAI key: %s', e)
        return None


def _user_model(user) -> Optional[str]:
    if user is None:
        return None
    m = getattr(user, 'xai_model', None)
    return (m or '').strip() or None


def is_configured(user=None) -> bool:
    """True if this user (or server fallback) can call Grok."""
    if _user_api_key(user):
        return True
    # Server env fallback (optional single-tenant only)
    return bool(os.getenv('XAI_API_KEY'))


def resolve_api_key(user=None) -> Optional[str]:
    """Prefer encrypted per-user key; optional env fallback."""
    uk = _user_api_key(user)
    if uk:
        return uk
    env = (os.getenv('XAI_API_KEY') or '').strip()
    return env or None


def _client(api_key: str):
    from openai import OpenAI
    if not api_key:
        raise RuntimeError('No xAI API key available for this user')
    return OpenAI(api_key=api_key, base_url=BASE_URL)


def _chat(
    messages: List[Dict[str, str]],
    *,
    user=None,
    temperature: float = 0.3,
) -> str:
    api_key = resolve_api_key(user)
    if not api_key:
        raise RuntimeError(
            'No xAI API key. Add yours under Settings → profile (encrypted), '
            'or set XAI_API_KEY on the server for single-tenant use.'
        )
    client = _client(api_key)
    model = _user_model(user) or DEFAULT_MODEL
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        return (resp.choices[0].message.content or '').strip()
    except Exception as e1:
        logger.warning('chat.completions failed (%s); trying responses API', e1)
        try:
            parts = []
            for m in messages:
                parts.append(f"{m.get('role', 'user').upper()}: {m.get('content', '')}")
            resp = client.responses.create(model=model, input='\n\n'.join(parts))
            text = getattr(resp, 'output_text', None)
            if text:
                return text.strip()
            return str(resp)
        except Exception as e2:
            logger.error('xAI call failed: %s', e2)
            raise RuntimeError(f'Grok API error: {e2}') from e2


def parse_goal_with_grok(
    text: str,
    *,
    inventory_summary: str,
    profile_summary: str,
    defaults: Optional[dict] = None,
    user=None,
) -> Dict[str, Any]:
    defaults = defaults or {}
    system = """You are an equity compensation tax planning assistant for VestX.
Parse the user's request into a strict JSON object for a deterministic optimizer.
Do NOT invent share quantities or tax amounts — only interpret intent.

Return ONLY valid JSON (no markdown) with keys:
{
  "target_net_cash": number or null,
  "objective": "min_tax" | "min_shares" | "max_net",
  "allow_rsu": boolean,
  "allow_iso_sell_held": boolean,
  "allow_iso_cashless": boolean,
  "allow_iso_exercise_hold": boolean,
  "iso_prefer_hold_fraction": number or null,
  "iso_max_exercise": number or null,
  "max_tax": number or null,
  "interpretation": "one sentence restating the goal",
  "clarifications": ["optional questions if ambiguous"]
}

Rules:
- "net $500k" / "take home 500k" / "after tax 500000" → target_net_cash
- Prefer min_tax unless user says minimize shares sold
- allow_iso_cashless true unless user forbids ordinary-income sales
- allow_iso_exercise_hold true if they mention hold/AMT/QD path
"""
    user_msg = f"""Profile: {profile_summary}

Inventory snapshot:
{inventory_summary}

Defaults: {json.dumps(defaults, default=str)}

User request:
{text}
"""
    raw = _chat(
        [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_msg},
        ],
        user=user,
        temperature=0.1,
    )
    return _extract_json(raw)


def explain_plan_with_grok(
    *,
    user_request: str,
    plan: dict,
    profile_summary: str,
    inventory_summary: str,
    user=None,
) -> str:
    system = """You are a CPA-literate equity tax educator embedded in VestX.
Explain a computed plan in clear prose for a sophisticated employee shareholder.
You must NOT change the numbers — the plan is authoritative.
Cover: what to sell (which lots), why those lots, ISO exercise vs sale if any,
federal vs California nuances, AMT/credit if relevant, risks and what to confirm with a CPA.
Be concise but precise. Use short sections with headings.
Disclaimer: planning estimate, not tax advice.
"""
    # Prefer compact plan + short profile/inventory already provided by caller
    from app.utils.account_context import _plan_compact
    user_msg = (
        f"User: {user_request}\n"
        f"{profile_summary}\n"
        f"{inventory_summary}\n"
        f"{_plan_compact(plan) if plan else 'plan: none'}"
    )
    # Cap explain payload hard — plan detail is in compact form
    if len(user_msg) > 3500:
        user_msg = user_msg[:3500] + '\n…'
    return _chat(
        [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_msg},
        ],
        user=user,
        temperature=0.4,
    )


def advisor_chat(
    *,
    messages: List[Dict[str, str]],
    plan: Optional[dict] = None,
    profile_summary: str = '',
    inventory_summary: str = '',
    user=None,
    account_context: Optional[str] = None,
    max_history: int = 10,
) -> str:
    """
    Chat with full account TSV + optional ENGINE_RESULT.

    Output contract matches the chat UI markdown renderer (headers, tables, lists).
    """
    system = f"""You are VestX Advisor for this user's private equity account.

## Data you have
- ACCOUNT_DATA below: full tax profile, grants, ALL lots (SpecID vest_id), sales, exercises, live price (TSV).
- ENGINE_RESULT (if present at top): deterministic VestX tax/goal engine output — **authoritative for dollar figures and recommended picks**. Do not invent different $ or pick lists.

## How to answer
1. Prefer ENGINE_RESULT numbers when present; explain *why* those picks fit the data.
2. Otherwise reason from ACCOUNT_DATA (cite vest_id, share_type, LT/ST, basis/strike).
3. CA: capital gains taxed as ordinary; MHST 1% over $1M taxable; CA AMT ~7% may apply on ISO bargain.
4. ISO: exercise ≠ sale; QD = 2 years from grant AND 1 year from exercise.
5. Planning-grade only — not a CPA or tax filing.

## OUTPUT FORMAT (required — UI renders Markdown)
Use clean GitHub-flavored Markdown the chat window can display:
- Start with a one-line **summary**
- Use `##` section headings (e.g. Recommendation, Numbers, SpecID lots, Risks, Next step)
- Use bullet lists for steps
- Use markdown tables for multi-lot comparisons:
  | vest_id | action | shares | reason |
  | --- | --- | --- | --- |
- Bold key $ amounts: **$500,000**
- Keep tone professional; no ASCII art; no raw HTML
- Length: thorough when comparing options (2–4 short sections), not a wall of unformatted text

## ACCOUNT_DATA
{account_context or '(empty)'}
"""

    trimmed: List[Dict[str, str]] = []
    for m in messages[-max_history:]:
        role = m.get('role') or 'user'
        if role not in ('user', 'assistant'):
            continue
        content = (m.get('content') or '')[:6000]
        trimmed.append({'role': role, 'content': content})

    api_messages = [{'role': 'system', 'content': system}]
    api_messages.extend(trimmed)
    return _chat(api_messages, user=user, temperature=0.4)


def compact_plan_for_explain(plan: dict) -> str:
    """Small plan blob for one-shot explain calls."""
    from app.utils.account_context import _plan_compact
    return _plan_compact(plan)


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            return json.loads(m.group(0))
        raise ValueError(f'Could not parse JSON from model: {text[:200]}')


def summarize_inventory(lots: List[dict]) -> str:
    lines = []
    for lot in lots[:40]:
        lines.append(
            f"- {lot.get('label')}: type={lot.get('share_type')} "
            f"held={lot.get('shares_available')} unex={lot.get('shares_unexercised')} "
            f"basis={lot.get('cost_basis_per_share')} strike={lot.get('strike_price')} "
            f"LT={lot.get('is_long_term')} ex={lot.get('exercise_date')}"
        )
    if len(lots) > 40:
        lines.append(f"... +{len(lots) - 40} more lots")
    return '\n'.join(lines) if lines else 'No lots.'


def summarize_profile(profile_dict: dict) -> str:
    return (
        f"filing={profile_dict.get('filing_status')} state={profile_dict.get('state_code')} "
        f"year={profile_dict.get('tax_year')} other_ordinary={profile_dict.get('other_ordinary_income')} "
        f"fed_amt_credit={profile_dict.get('amt_credit_carryforward')} "
        f"ca_amt_credit={profile_dict.get('ca_amt_credit_carryforward')}"
    )
