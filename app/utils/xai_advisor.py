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
# grok-4.5/4.6 default to "high" reasoning — chat Qs feel frozen without this.
REASONING_EFFORT = (os.getenv('XAI_REASONING_EFFORT') or 'low').strip() or 'low'


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
    # Seconds. Reasoning models (grok-4.5+) can exceed the SDK default.
    # Use a float — do not import httpx; Railway's openai wheel vendors httpx2.
    return OpenAI(
        api_key=api_key,
        base_url=BASE_URL,
        timeout=600.0,
    )


def _message_text(resp) -> str:
    """Pull assistant text from chat.completions (incl. reasoning-only replies)."""
    try:
        msg = resp.choices[0].message
    except Exception:
        return ''
    text = (getattr(msg, 'content', None) or '').strip()
    if text:
        return text
    for attr in ('reasoning_content',):
        extra = getattr(msg, attr, None)
        if extra:
            return str(extra).strip()
    return ''


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
    effort = REASONING_EFFORT
    create_kwargs = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
    }
    try:
        try:
            resp = client.chat.completions.create(
                **create_kwargs, reasoning_effort=effort,
            )
        except TypeError:
            resp = client.chat.completions.create(
                **create_kwargs, extra_body={'reasoning_effort': effort},
            )
        text = _message_text(resp)
        if text:
            return text
        logger.warning('chat.completions returned empty content; trying responses API')
        raise RuntimeError('empty chat.completions content')
    except Exception as e1:
        logger.warning('chat.completions failed (%s); trying responses API', e1)
        try:
            parts = []
            for m in messages:
                parts.append(f"{m.get('role', 'user').upper()}: {m.get('content', '')}")
            try:
                resp = client.responses.create(
                    model=model,
                    input='\n\n'.join(parts),
                    reasoning={'effort': effort},
                )
            except TypeError:
                resp = client.responses.create(
                    model=model,
                    input='\n\n'.join(parts),
                    extra_body={'reasoning': {'effort': effort}},
                )
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
  "exercise_all_iso": boolean,
  "iso_prefer_hold_fraction": number or null,
  "iso_max_exercise": number or null,
  "max_tax": number or null,
  "interpretation": "one sentence restating the goal",
  "clarifications": ["optional questions if ambiguous"]
}

Rules:
- "net $500k" / "take home 500k" / "after tax 500000" → target_net_cash (pocket after costs)
- Prefer min_tax unless user says minimize shares sold
- allow_iso_cashless true unless user forbids ordinary-income sales
- allow_iso_exercise_hold true if they mention hold/AMT/QD path
- exercise_all_iso true if they want to exercise all/every ISO without selling ISO stock
- If fund/cover strike or AMT via RSU sales + exercise hold → exercise_all_iso true, allow_iso_cashless false, iso_prefer_hold_fraction 1.0
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
    max_history: int = 8,
) -> str:
    """
    Chat with full account data.

    Models attend more to the *latest user message* than a huge system dump, so we:
    - keep system instructions short
    - attach ACCOUNT_DATA to the final user turn
    """
    system = """You are VestX Advisor for one logged-in user's equity account.

You receive ACCOUNT_DATA with every question (readable summary + full lot TSV).
Treat ACCOUNT_DATA as ground truth for holdings. Cite real vest_id values only.
If ENGINE_RESULT appears in ACCOUNT_DATA, its dollars and SpecID picks are authoritative — explain them; do not invent alternate $ picks.
If the summary says no lots, say inventory is empty — never fabricate grants/lots.

Tax notes: CA taxes capital gains as ordinary; MHST 1% over $1M; ISO exercise ≠ sale; QD = 2y grant + 1y exercise.
Planning-grade only (not a CPA).

## OUTPUT (UI renders Markdown)
1. One-line **summary**
2. `##` sections (e.g. What I see in your account, Recommendation, SpecID lots, Risks, Next step)
3. Bullet lists; markdown tables for multi-lot comps:
   | vest_id | type | shares | basis | notes |
   | --- | --- | --- | --- | --- |
4. Bold key amounts like **$500,000**
5. Start by reflecting 1–2 concrete facts from READABLE_SUMMARY or LOTS_TSV so the user sees you read the data
6. No raw HTML, no ASCII art
"""

    # Prior turns only (exclude last user — we re-wrap it with data)
    prior: List[Dict[str, str]] = []
    last_user = ''
    for m in messages:
        role = m.get('role') or 'user'
        if role not in ('user', 'assistant'):
            continue
        content = (m.get('content') or '')[:5000]
        if role == 'user':
            last_user = content
        prior.append({'role': role, 'content': content})

    # Drop the final user message from prior; we'll re-add with context
    if prior and prior[-1]['role'] == 'user':
        prior = prior[:-1]
    # Cap history length
    prior = prior[-(max_history - 1) :] if max_history > 1 else []

    acct = (account_context or '').strip() or '(no account data loaded)'
    # Hard cap to keep request under model limits (~100k chars is plenty for hundreds of lots)
    if len(acct) > 90000:
        acct = acct[:90000] + '\n…[truncated]'

    wrapped_user = (
        f"ACCOUNT_DATA (authoritative for this user — read carefully):\n"
        f"{acct}\n\n"
        f"---\n"
        f"USER_QUESTION:\n{last_user or '(empty)'}\n\n"
        f"Answer using ACCOUNT_DATA. Quote specific vest_id / share counts from the data."
    )

    api_messages = [{'role': 'system', 'content': system}]
    api_messages.extend(prior)
    api_messages.append({'role': 'user', 'content': wrapped_user})
    return _chat(api_messages, user=user, temperature=0.35)


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
