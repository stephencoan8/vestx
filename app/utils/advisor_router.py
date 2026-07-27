"""
Route chat questions: deterministic engine first, Grok only for nuance.

Computable (no LLM tokens for the numbers):
  - Net-cash / min-tax lot selection → goal_optimizer
  - Portfolio totals / held value
  - ISO earliest QD dates
  - Tax profile readout

Grok (optional, after engine):
  - "why / explain / risk / should I" on top of ENGINE_RESULT
  - Open-ended strategy judgment

This keeps accuracy high and API cost low.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from app.utils.goal_optimizer import (
    GoalRequest,
    optimize_goal,
    parse_goal_heuristic,
)
from app.utils.tax_engine import earliest_qualifying_sale_date, classify_iso_disposition
from app.utils.account_context import estimate_tokens


_NET_CASH = re.compile(
    r'(?:net|after[\s-]?tax|take[\s-]?home|cash(?:\s+out)?)\s*\$?\s*([\d,]+(?:\.\d+)?)\s*([kmb])?'
    r'|\$\s*([\d,]+(?:\.\d+)?)\s*([kmb])?\s*(?:net|after[\s-]?tax)',
    re.I,
)
_OPTIMIZE = re.compile(
    r'\b(which lots?|what (?:should|do) i sell|minimi[sz]e tax|optimal|optimize|'
    r'specid|sell order|how many shares)\b',
    re.I,
)
_PORTFOLIO = re.compile(
    r'\b(how many shares|portfolio|what do i (?:own|hold|have)|total value|'
    r'unrealized|inventory summary)\b',
    re.I,
)
_QD = re.compile(
    r'\b(qualifying|qd date|when can i sell.*iso|holding period|disqualif)\b',
    re.I,
)
_PROFILE = re.compile(
    r'\b(tax profile|my (?:filing|wages|bracket|state)|what rate)\b',
    re.I,
)
_NUANCE = re.compile(
    r'\b(why|explain|nuance|risk|should i|pros?|cons?|tradeoff|compared? to|'
    r'what if|opinion|advice|think)\b',
    re.I,
)


def _parse_money(m: re.Match) -> Optional[float]:
    g = m.groups()
    # groups: (amt1, suf1, amt2, suf2) depending on pattern
    if g[0]:
        val, suf = float(g[0].replace(',', '')), (g[1] or '').lower()
    elif len(g) > 2 and g[2]:
        val, suf = float(g[2].replace(',', '')), (g[3] or '').lower()
    else:
        return None
    if suf == 'k':
        val *= 1_000
    elif suf == 'm':
        val *= 1_000_000
    elif suf == 'b':
        val *= 1_000_000_000
    return val


@dataclass
class RouterResult:
    mode: str  # engine_only | engine_then_grok | grok_only
    intent: str
    engine_text: str = ''
    engine_payload: Dict[str, Any] = field(default_factory=dict)
    deterministic_reply: str = ''  # human-readable if engine_only
    skip_grok: bool = False
    notes: List[str] = field(default_factory=list)

    def to_meta(self) -> dict:
        return {
            'mode': self.mode,
            'intent': self.intent,
            'skip_grok': self.skip_grok,
            'engine_est_tokens': estimate_tokens(self.engine_text + self.deterministic_reply),
            'notes': self.notes,
        }


def route_and_compute(
    *,
    user_message: str,
    profile_dict: dict,
    inventory_lots: list,
    live_price: float,
    sale_date: Optional[date] = None,
    plan: Optional[dict] = None,
    force_grok: bool = False,
) -> RouterResult:
    """
    Run deterministic tools when the question is computable.
    """
    text = (user_message or '').strip()
    if not text:
        return RouterResult(mode='grok_only', intent='empty', notes=['empty message'])

    wants_nuance = bool(_NUANCE.search(text)) or force_grok
    today = sale_date or date.today()
    price = float(live_price or 0)

    # --- Net cash / optimize lots ---
    net_m = _NET_CASH.search(text)
    if net_m or _OPTIMIZE.search(text):
        target = _parse_money(net_m) if net_m else None
        heur = parse_goal_heuristic(text, {
            'sale_price': price,
            'sale_date': today,
            'exercise_date': today,
            'exercise_fmv': price,
        })
        if target is None:
            target = heur.target_net_cash

        goal = GoalRequest(
            target_net_cash=target,
            objective=heur.objective or 'min_tax',
            sale_price=price,
            sale_date=today,
            exercise_date=today,
            exercise_fmv=price,
            allow_rsu=heur.allow_rsu,
            allow_iso_sell_held=heur.allow_iso_sell_held,
            allow_iso_cashless=heur.allow_iso_cashless,
            allow_iso_exercise_hold=heur.allow_iso_exercise_hold,
            raw_text=text,
        )
        result = optimize_goal(profile_dict, inventory_lots, goal)
        payload = result.to_dict()

        lines = [
            'ENGINE_RESULT (deterministic goal optimizer — trust these $):',
            f"target_net=${target or 0:,.0f} objective={goal.objective}",
            f"success={result.success} achieved_net=${result.achieved_net_cash:,.0f} "
            f"shortfall=${result.shortfall:,.0f}",
            f"proceeds=${result.total_proceeds:,.0f} tax=${result.total_tax:,.0f} "
            f"strike_outlay=${result.total_strike_outlay:,.0f} "
            f"eff_rate={result.effective_tax_rate*100:.1f}%",
            'picks (SpecID order):',
        ]
        for p in result.picks:
            lines.append(
                f"  v{p.vest_event_id} {p.action} {p.shares:.2f}sh "
                f"@${p.price:.2f} basis/k=${p.basis_or_strike:.2f} "
                f"{'LT' if p.is_long_term else 'ST'} {p.iso_disposition} — {p.reason}"
            )
        if not result.picks and result.actions_summary:
            lines.extend(f"  {a}" for a in result.actions_summary[:12])
        for n in (result.efficiency_notes or [])[:4]:
            lines.append(f"note: {n}")

        engine_text = '\n'.join(lines)
        human = _format_goal_reply(result, target)

        if wants_nuance:
            return RouterResult(
                mode='engine_then_grok',
                intent='goal_optimize',
                engine_text=engine_text,
                engine_payload=payload,
                deterministic_reply=human,
                skip_grok=False,
                notes=['Ran goal_optimizer; Grok will only explain, not recompute $'],
            )
        return RouterResult(
            mode='engine_only',
            intent='goal_optimize',
            engine_text=engine_text,
            engine_payload=payload,
            deterministic_reply=human,
            skip_grok=True,
            notes=['Answered from goal_optimizer only (0 Grok tokens)'],
        )

    # --- Portfolio snapshot ---
    if _PORTFOLIO.search(text):
        held = sum(float(l.get('shares_available') or 0) for l in inventory_lots)
        unex = sum(float(l.get('shares_unexercised') or 0) for l in inventory_lots)
        rsu_h = sum(
            float(l.get('shares_available') or 0)
            for l in inventory_lots if not l.get('is_iso')
        )
        iso_h = sum(
            float(l.get('shares_available') or 0)
            for l in inventory_lots if l.get('is_iso')
        )
        ug = sum(float(l.get('unrealized_gain') or 0) for l in inventory_lots)
        val = held * price
        human = (
            f"**Portfolio (deterministic)** @ ${price:,.2f}/sh\n"
            f"- Sellable held: {held:,.2f} sh (~${val:,.0f})\n"
            f"  - RSU held: {rsu_h:,.2f}\n"
            f"  - ISO held (exercised): {iso_h:,.2f}\n"
            f"- Unexercised ISO: {unex:,.2f} sh\n"
            f"- Lots: {len(inventory_lots)}\n"
            f"- Unrealized on held (approx): ${ug:,.0f}\n\n"
            f"For min-tax sell picks, ask e.g. “net $500k minimize tax”."
        )
        engine = (
            f"ENGINE_RESULT portfolio: px={price} held={held} unexISO={unex} "
            f"rsu_h={rsu_h} iso_h={iso_h} lots={len(inventory_lots)} urGain={ug} val={val}"
        )
        if wants_nuance:
            return RouterResult(
                mode='engine_then_grok', intent='portfolio',
                engine_text=engine, deterministic_reply=human, skip_grok=False,
            )
        return RouterResult(
            mode='engine_only', intent='portfolio',
            engine_text=engine, deterministic_reply=human, skip_grok=True,
            notes=['Portfolio totals from lot_inventory (0 Grok tokens)'],
        )

    # --- ISO QD calendar ---
    if _QD.search(text):
        lines_h = ['**ISO holding / QD (deterministic)**\n']
        eng_lines = ['ENGINE_RESULT iso_qd:']
        any_iso = False
        for l in inventory_lots:
            if not l.get('is_iso'):
                continue
            any_iso = True
            gd = l.get('grant_date')
            ed = l.get('exercise_date')
            if isinstance(gd, str):
                gd = date.fromisoformat(gd[:10])
            if isinstance(ed, str):
                ed = date.fromisoformat(ed[:10]) if ed else None
            unex = float(l.get('shares_unexercised') or 0)
            held = float(l.get('shares_available') or 0)
            if ed:
                qd = earliest_qualifying_sale_date(gd, ed)
                disp = classify_iso_disposition(gd, ed, today)
                lines_h.append(
                    f"- v{l.get('vest_event_id')}: held {held:.2f} sh · "
                    f"ex {ed} · earliest QD **{qd}** · if sold today → **{disp}**"
                )
                eng_lines.append(
                    f"v{l.get('vest_event_id')} held={held} ex={ed} qd={qd} today={disp}"
                )
            elif unex > 0:
                # Exercise today → QD from max(grant+2y, today+1y)
                qd_if = earliest_qualifying_sale_date(gd, today)
                lines_h.append(
                    f"- v{l.get('vest_event_id')}: **{unex:.2f} unexercised** · "
                    f"if exercise today {today} → earliest QD **{qd_if}**"
                )
                eng_lines.append(
                    f"v{l.get('vest_event_id')} unex={unex} if_ex_today qd={qd_if}"
                )
        if not any_iso:
            human = 'No ISO lots in inventory.'
        else:
            human = '\n'.join(lines_h)
        engine = '\n'.join(eng_lines)
        if wants_nuance:
            return RouterResult(
                mode='engine_then_grok', intent='iso_qd',
                engine_text=engine, deterministic_reply=human, skip_grok=False,
            )
        return RouterResult(
            mode='engine_only', intent='iso_qd',
            engine_text=engine, deterministic_reply=human, skip_grok=True,
            notes=['QD dates from tax_engine (0 Grok tokens)'],
        )

    # --- Tax profile ---
    if _PROFILE.search(text):
        p = profile_dict
        human = (
            f"**Tax profile (deterministic)**\n"
            f"- Filing: {p.get('filing_status')} · State: {p.get('state_code')} · "
            f"Year: {p.get('tax_year')}\n"
            f"- Other ordinary income: ${float(p.get('other_ordinary_income') or 0):,.0f}\n"
            f"- YTD wages: ${float(p.get('ytd_wages') or 0):,.0f}\n"
            f"- Fed AMT credit: ${float(p.get('amt_credit_carryforward') or 0):,.0f}\n"
            f"- CA AMT credit: ${float(p.get('ca_amt_credit_carryforward') or 0):,.0f}\n"
            f"- State engine: {p.get('use_state_engine')} · NIIT: {p.get('include_niit')}\n"
        )
        engine = f"ENGINE_RESULT profile: {p}"
        if wants_nuance:
            return RouterResult(
                mode='engine_then_grok', intent='profile',
                engine_text=str(engine)[:1500], deterministic_reply=human, skip_grok=False,
            )
        return RouterResult(
            mode='engine_only', intent='profile',
            engine_text=str(engine)[:1500], deterministic_reply=human, skip_grok=True,
        )

    # --- Existing plan in session ---
    if plan and re.search(r'\b(this plan|my plan|the plan|those picks)\b', text, re.I):
        from app.utils.account_context import _plan_compact
        engine = 'ENGINE_RESULT existing_plan:\n' + _plan_compact(plan)
        if wants_nuance or True:
            # Explaining a plan almost always wants prose — still engine numbers first
            return RouterResult(
                mode='engine_then_grok', intent='explain_plan',
                engine_text=engine, deterministic_reply='', skip_grok=False,
                notes=['Plan numbers from prior engine run'],
            )

    return RouterResult(
        mode='grok_only',
        intent='open',
        engine_text='',
        skip_grok=False,
        notes=['Open-ended; Grok + compact account snapshot'],
    )


def _format_goal_reply(result, target) -> str:
    lines = [
        '**Goal optimizer (deterministic engine)**',
        '',
    ]
    if target:
        lines.append(f"Target net cash: **${target:,.0f}**")
    lines.append(
        f"Achieved net: **${result.achieved_net_cash:,.0f}** · "
        f"Tax: **${result.total_tax:,.0f}** · "
        f"Shortfall: **${result.shortfall:,.0f}** · "
        f"{'Target met' if result.success else 'Target not fully met'}"
    )
    lines.append(
        f"Gross proceeds: ${result.total_proceeds:,.0f} · "
        f"Strike outlay: ${result.total_strike_outlay:,.0f} · "
        f"Eff. rate: {result.effective_tax_rate*100:.1f}%"
    )
    lines.append('')
    lines.append('**SpecID picks** (sell/exercise these lots):')
    if not result.picks:
        for a in (result.actions_summary or [])[:15]:
            lines.append(f"- {a}")
    else:
        for p in result.picks:
            lines.append(
                f"- **v{p.vest_event_id}** `{p.action}` **{p.shares:,.2f}** sh "
                f"@ ${p.price:.2f} ({'LT' if p.is_long_term else 'ST'}) — {p.reason}"
            )
    lines.append('')
    lines.append(
        '_Numbers from VestX tax/goal engines. '
        'Ask “why this plan?” if you want a Grok explanation of tradeoffs._'
    )
    return '\n'.join(lines)
