"""
SpaceXAI / xAI Grok advisor for equity planning.

Deterministic math stays in goal_optimizer + tax_engine.
Grok is used for:
  1) Parsing natural-language goals into GoalRequest JSON
  2) Explaining tradeoffs / nuance of a computed plan
  3) Free-form Q&A with plan + inventory context

Requires XAI_API_KEY (server-side only). OpenAI-compatible API at api.x.ai.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv('XAI_MODEL', 'grok-4.5')
BASE_URL = os.getenv('XAI_BASE_URL', 'https://api.x.ai/v1')


def is_configured() -> bool:
    return bool(os.getenv('XAI_API_KEY'))


def _client():
    from openai import OpenAI
    key = os.getenv('XAI_API_KEY')
    if not key:
        raise RuntimeError('XAI_API_KEY is not set')
    return OpenAI(api_key=key, base_url=BASE_URL)


def _chat(messages: List[Dict[str, str]], *, temperature: float = 0.3) -> str:
    """
    Call Grok via OpenAI-compatible chat completions (widely supported).
    Falls back to responses API if needed.
    """
    client = _client()
    model = DEFAULT_MODEL
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
            # Flatten messages for responses API
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
) -> Dict[str, Any]:
    """
    Return dict suitable for GoalRequest fields + confidence notes.
    """
    defaults = defaults or {}
    system = """You are an equity compensation tax planning assistant for VestX.
Parse the user's request into a strict JSON object for a deterministic optimizer.
Do NOT invent share quantities or tax amounts — only interpret intent.

Return ONLY valid JSON (no markdown) with keys:
{
  "target_net_cash": number or null,  // dollars the user wants to keep after tax
  "objective": "min_tax" | "min_shares" | "max_net",
  "allow_rsu": boolean,
  "allow_iso_sell_held": boolean,
  "allow_iso_cashless": boolean,
  "allow_iso_exercise_hold": boolean,
  "iso_prefer_hold_fraction": number or null,  // 0-1
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
    user = f"""Profile: {profile_summary}

Inventory snapshot:
{inventory_summary}

Defaults (price/dates may already be set in UI): {json.dumps(defaults, default=str)}

User request:
{text}
"""
    raw = _chat(
        [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        temperature=0.1,
    )
    return _extract_json(raw)


def explain_plan_with_grok(
    *,
    user_request: str,
    plan: dict,
    profile_summary: str,
    inventory_summary: str,
) -> str:
    system = """You are a CPA-literate equity tax educator embedded in VestX.
Explain a computed plan in clear prose for a sophisticated employee shareholder.
You must NOT change the numbers — the plan is authoritative.
Cover: what to sell (which lots), why those lots, ISO exercise vs sale if any,
federal vs California nuances, AMT/credit if relevant, risks and what to confirm with a CPA.
Be concise but precise. Use short sections with headings.
Disclaimer: planning estimate, not tax advice.
"""
    user = f"""User asked: {user_request}

Profile: {profile_summary}

Inventory:
{inventory_summary}

Computed plan JSON:
{json.dumps(plan, indent=2, default=str)[:12000]}
"""
    return _chat(
        [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        temperature=0.4,
    )


def advisor_chat(
    *,
    messages: List[Dict[str, str]],
    plan: Optional[dict],
    profile_summary: str,
    inventory_summary: str,
) -> str:
    system = f"""You are VestX Advisor powered by Grok. Help the logged-in user plan RSU/ISO sales and exercises.
Deterministic engine owns calculations. You explain tradeoffs and suggest what to run next in the optimizer.
Never invent exact tax dollars that contradict the plan JSON when one is provided.

Profile: {profile_summary}

Inventory:
{inventory_summary}

Current plan (if any):
{json.dumps(plan, indent=2, default=str)[:8000] if plan else 'None yet — suggest setting a net cash goal.'}
"""
    api_messages = [{'role': 'system', 'content': system}]
    for m in messages[-12:]:
        role = m.get('role') or 'user'
        if role not in ('user', 'assistant', 'system'):
            role = 'user'
        api_messages.append({'role': role, 'content': m.get('content') or ''})
    return _chat(api_messages, temperature=0.45)


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    # Strip markdown fences
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
