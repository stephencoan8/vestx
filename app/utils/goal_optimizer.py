"""
Goal-based equity optimizer.

User states an outcome (e.g. net $500k cash). Deterministic engine selects
specific lots (SpecID) and ISO exercise vs sell splits to hit the goal while
minimizing tax (or shares sold).

Does not use the LLM for numbers — only for optional NL parsing / explanation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.utils.equity_planner import (
    LotSpec,
    plan_iso_cashless_dd,
    plan_iso_exercise_hold,
    plan_iso_sell_held,
    plan_rsu_sell,
    plan_mixed_default,
    _build_sale,
    _profile_for_year,
)
from app.utils.tax_engine import (
    LotSaleInput,
    ExerciseInput,
    analyze_sales,
    classify_iso_disposition,
    earliest_qualifying_sale_date,
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class GoalRequest:
    """Structured goal — filled by form or Grok parse."""
    target_net_cash: Optional[float] = None
    objective: str = 'min_tax'  # min_tax | min_shares | max_net
    sale_price: float = 0.0
    sale_date: Optional[date] = None
    exercise_date: Optional[date] = None
    exercise_fmv: Optional[float] = None
    allow_rsu: bool = True
    allow_iso_sell_held: bool = True
    allow_iso_cashless: bool = True
    allow_iso_exercise_hold: bool = False  # hold burns cash; off by default for net-cash goals
    # Exercise every unexercised ISO (hold — do not sell ISO stock). Strike + AMT
    # stack into the same tax engine run; RSU sales fund costs + pocket target.
    exercise_all_iso: bool = False
    # Optional ISO allocation hints
    iso_max_exercise: Optional[float] = None  # max unexercised to touch
    iso_prefer_hold_fraction: Optional[float] = None  # 0..1 of exercised amount to hold
    max_tax: Optional[float] = None
    raw_text: str = ''

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.sale_date:
            d['sale_date'] = self.sale_date.isoformat()
        if self.exercise_date:
            d['exercise_date'] = self.exercise_date.isoformat()
        return d


@dataclass
class LotPick:
    vest_event_id: int
    grant_id: int
    label: str
    share_type: str
    is_iso: bool
    action: str  # sell_rsu | sell_iso_held | iso_cashless_dd | iso_exercise_hold
    shares: float
    price: float
    basis_or_strike: float
    estimated_gain: float
    is_long_term: bool
    iso_disposition: str  # n/a | qualifying | disqualifying | hold
    rank_score: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GoalPlanResult:
    success: bool
    goal: Dict[str, Any]
    achieved_net_cash: float
    shortfall: float
    total_proceeds: float
    total_tax: float
    total_strike_outlay: float
    effective_tax_rate: float
    picks: List[LotPick]
    actions_summary: List[str]
    efficiency_notes: List[str]
    tax_analysis: Optional[Dict[str, Any]] = None
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    iso_split: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'success': self.success,
            'goal': self.goal,
            'achieved_net_cash': self.achieved_net_cash,
            'shortfall': self.shortfall,
            'total_proceeds': self.total_proceeds,
            'total_tax': self.total_tax,
            'total_strike_outlay': self.total_strike_outlay,
            'effective_tax_rate': self.effective_tax_rate,
            'picks': [p.to_dict() for p in self.picks],
            'actions_summary': self.actions_summary,
            'efficiency_notes': self.efficiency_notes,
            'tax_analysis': self.tax_analysis,
            'alternatives': self.alternatives,
            'warnings': self.warnings,
            'iso_split': self.iso_split,
        }


# ---------------------------------------------------------------------------
# Inventory → candidate pool
# ---------------------------------------------------------------------------

def inventory_to_specs(lots: Sequence[dict], price: float) -> List[LotSpec]:
    """Convert lot_inventory dicts to LotSpec with max available shares for planning."""
    from app.utils.shares import whole_shares

    specs: List[LotSpec] = []
    for lot in lots:
        try:
            is_iso = bool(lot.get('is_iso'))
            held = float(whole_shares(lot.get('shares_available') or 0))
            unex = float(whole_shares(lot.get('shares_unexercised') or 0))
            if held <= 0 and unex <= 0:
                continue
            ex_raw = lot.get('exercise_date')
            ex_date = None
            if ex_raw:
                try:
                    ex_date = date.fromisoformat(str(ex_raw)[:10]) if not isinstance(ex_raw, date) else ex_raw
                except Exception:
                    ex_date = None
            vd = lot.get('vest_date') or lot.get('grant_date')
            gd = lot.get('grant_date') or lot.get('vest_date')
            if not vd or not gd:
                continue
            vest_date = date.fromisoformat(str(vd)[:10]) if not isinstance(vd, date) else vd
            grant_date = date.fromisoformat(str(gd)[:10]) if not isinstance(gd, date) else gd
            specs.append(
                LotSpec(
                    vest_event_id=int(lot['vest_event_id']),
                    grant_id=int(lot.get('grant_id') or lot['vest_event_id']),
                    share_type=lot.get('share_type') or 'rsu',
                    grant_type=lot.get('grant_type') or '',
                    is_iso=is_iso,
                    shares=held if held > 0 else 0.0,
                    vest_date=vest_date,
                    grant_date=grant_date,
                    strike_price=float(lot.get('strike_price') or lot.get('cost_basis_per_share') or 0),
                    cost_basis_per_share=float(lot.get('cost_basis_per_share') or 0),
                    exercise_date=ex_date,
                    fmv_at_exercise=(
                        float(lot['fmv_at_exercise'])
                        if lot.get('fmv_at_exercise') is not None
                        else None
                    ),
                    shares_available=held,
                    shares_unexercised=unex,
                    label=lot.get('label') or f"vest {lot['vest_event_id']}",
                )
            )
            if held > 0 and unex > 0:
                specs[-1].shares_unexercised = unex
            elif unex > 0 and held <= 0:
                specs[-1].shares = 0.0
                specs[-1].shares_unexercised = unex
        except Exception:
            continue
    return specs


def _clone_spec(s: LotSpec, shares: float) -> LotSpec:
    from app.utils.shares import whole_shares

    n = deepcopy(s)
    n.shares = float(whole_shares(shares))
    return n


# ---------------------------------------------------------------------------
# Tax-efficiency ranking (deterministic heuristics + engine refinement)
# ---------------------------------------------------------------------------

def _lot_rank_score(
    spec: LotSpec,
    *,
    price: float,
    sale_date: date,
    mode: str,  # sell_held | cashless
) -> Tuple[float, str, bool, str]:
    """
    Lower score = sell first when minimizing tax for net cash.

    Factors:
    - Long-term capital gain preferred over short-term / ordinary
    - Higher basis (less gain per share) preferred
    - QD preferred over DD for ISO
    - RSU/LTCG preferred over ISO cashless ordinary
    """
    if mode == 'cashless':
        strike = spec.strike_price
        gain = max(0.0, price - strike)
        # Ordinary income heavy — high tax score
        # net ~ price - tax; tax rate ~ 40%+ combined on ordinary in CA
        tax_rate_est = 0.45
        net_per_sh = price * (1 - 0.15) - strike  # rough after ordinary
        # Prefer higher net efficiency but penalize ordinary
        score = 1000 + tax_rate_est * 100 + gain * 0.01
        return score, 'ISO cashless DD (ordinary income — last resort for liquidity)', False, 'disqualifying'

    basis = spec.cost_basis_per_share
    if spec.is_iso and spec.exercise_date:
        disp = classify_iso_disposition(spec.grant_date, spec.exercise_date, sale_date)
        is_lt = True if disp == 'qualifying' else (
            (sale_date - spec.exercise_date).days >= 365
        )
        if disp == 'qualifying':
            gain = max(0.0, price - spec.strike_price)
            tax_rate_est = 0.28  # ~15 fed + 3.8 niit + ~9 CA
            score = tax_rate_est * 100 + gain * 0.001
            reason = 'ISO QD → federal LTCG (efficient)'
            return score, reason, True, 'qualifying'
        else:
            fmv = spec.fmv_at_exercise if spec.fmv_at_exercise is not None else price
            ordinary = max(0.0, min(price, fmv) - spec.strike_price)
            residual = max(0.0, price - max(spec.strike_price, min(price, fmv)))
            tax_rate_est = 0.42
            score = 500 + tax_rate_est * 100 + ordinary * 0.01
            reason = 'ISO DD (held stock) — ordinary on bargain'
            return score, reason, is_lt, 'disqualifying'

    # RSU
    gain = max(0.0, price - basis)
    holding = (sale_date - spec.vest_date).days
    is_lt = holding >= 365
    if is_lt:
        tax_rate_est = 0.28
        score = tax_rate_est * 100 + gain * 0.001 - basis * 0.0001
        reason = 'RSU long-term capital gain (preferred)'
    else:
        tax_rate_est = 0.40
        score = 200 + tax_rate_est * 100 + gain * 0.001
        reason = 'RSU short-term (taxed as ordinary federally)'
    return score, reason, is_lt, 'n/a'


def _build_ranked_candidates(
    specs: Sequence[LotSpec],
    goal: GoalRequest,
    sale_date: date,
    price: float,
) -> List[Tuple[LotSpec, str, float, str, bool, str, float]]:
    """
    List of (spec_template, action, max_shares, reason, is_lt, disp, score)
    sorted by score ascending (best tax first).
    """
    cands = []
    for s in specs:
        held = s.shares_available if s.shares_available is not None else s.shares
        unex = s.shares_unexercised or 0.0

        if not s.is_iso and goal.allow_rsu and held > 0:
            score, reason, is_lt, disp = _lot_rank_score(
                s, price=price, sale_date=sale_date, mode='sell_held'
            )
            cands.append((s, 'sell_rsu', held, reason, is_lt, disp, score))

        if s.is_iso and goal.allow_iso_sell_held and held > 0 and s.exercise_date:
            score, reason, is_lt, disp = _lot_rank_score(
                s, price=price, sale_date=sale_date, mode='sell_held'
            )
            cands.append((s, 'sell_iso_held', held, reason, is_lt, disp, score))

        # When exercising-all and holding, never cashless-sell those ISOs
        if (
            s.is_iso
            and goal.allow_iso_cashless
            and unex > 0
            and not goal.exercise_all_iso
        ):
            score, reason, is_lt, disp = _lot_rank_score(
                s, price=price, sale_date=sale_date, mode='cashless'
            )
            max_ex = unex
            if goal.iso_max_exercise is not None:
                max_ex = min(max_ex, goal.iso_max_exercise)
            if max_ex > 0:
                cands.append((s, 'iso_cashless_dd', max_ex, reason, is_lt, disp, score))

    cands.sort(key=lambda x: (x[6], -x[2]))  # best score, then larger lots
    return cands


def _iso_exercise_hold_picks(
    specs: Sequence[LotSpec],
    *,
    price: float,
    fmv: float,
) -> List[LotPick]:
    """One iso_exercise_hold pick per unexercised ISO lot (whole shares)."""
    from app.utils.shares import whole_shares

    picks: List[LotPick] = []
    for s in specs:
        unex = float(whole_shares(s.shares_unexercised or 0))
        if not s.is_iso or unex <= 0:
            continue
        bargain = max(0.0, fmv - s.strike_price) * unex
        picks.append(
            LotPick(
                vest_event_id=s.vest_event_id,
                grant_id=s.grant_id,
                label=s.label,
                share_type=s.share_type,
                is_iso=True,
                action='iso_exercise_hold',
                shares=unex,
                price=fmv,
                basis_or_strike=s.strike_price,
                estimated_gain=bargain,
                is_long_term=False,
                iso_disposition='hold',
                rank_score=0.0,
                reason=(
                    f'Exercise-and-hold all unexercised ISO · strike ${s.strike_price:.2f} · '
                    f'AMT bargain ≈ ${bargain:,.0f}'
                ),
            )
        )
    return picks


# ---------------------------------------------------------------------------
# Evaluate a set of picks with full tax engine
# ---------------------------------------------------------------------------

def _evaluate_picks(
    profile: dict,
    picks: List[LotPick],
    *,
    sale_date: date,
    exercise_date: date,
    price: float,
    fmv: float,
) -> Dict[str, Any]:
    sales: List[LotSaleInput] = []
    exercises: List[ExerciseInput] = []
    strike_outlay = 0.0

    for p in picks:
        if p.shares <= 0:
            continue
        if p.action == 'sell_rsu':
            sales.append(
                LotSaleInput(
                    vest_event_id=p.vest_event_id,
                    grant_id=p.grant_id,
                    share_type=p.share_type,
                    grant_type='',
                    shares=p.shares,
                    sale_price=price,
                    sale_date=sale_date,
                    vest_date=sale_date,  # unused for CG if basis set
                    grant_date=sale_date,
                    cost_basis_per_share=p.basis_or_strike,
                    is_iso=False,
                    label=p.label,
                )
            )
        elif p.action == 'sell_iso_held':
            # Need exercise date on pick - encode in iso_disposition path via analyze
            # Reconstruct from pick fields
            sales.append(
                LotSaleInput(
                    vest_event_id=p.vest_event_id,
                    grant_id=p.grant_id,
                    share_type=p.share_type,
                    grant_type='',
                    shares=p.shares,
                    sale_price=price,
                    sale_date=sale_date,
                    vest_date=sale_date,
                    grant_date=sale_date,
                    cost_basis_per_share=p.basis_or_strike,
                    is_iso=True,
                    strike_price=p.basis_or_strike,
                    exercise_date=exercise_date,  # may be wrong — fixed below via specs
                    fmv_at_exercise=fmv,
                    label=p.label,
                )
            )
        elif p.action == 'iso_cashless_dd':
            sales.append(
                LotSaleInput(
                    vest_event_id=p.vest_event_id,
                    grant_id=p.grant_id,
                    share_type=p.share_type,
                    grant_type='',
                    shares=p.shares,
                    sale_price=price,
                    sale_date=sale_date,
                    vest_date=sale_date,
                    grant_date=sale_date,
                    cost_basis_per_share=p.basis_or_strike,
                    is_iso=True,
                    strike_price=p.basis_or_strike,
                    exercise_date=exercise_date,
                    fmv_at_exercise=fmv,
                    label=p.label,
                )
            )
            strike_outlay += p.basis_or_strike * p.shares
        elif p.action == 'iso_exercise_hold':
            exercises.append(
                ExerciseInput(
                    vest_event_id=p.vest_event_id,
                    shares=p.shares,
                    exercise_date=exercise_date,
                    strike_price=p.basis_or_strike,
                    fmv_at_exercise=fmv,
                    label=p.label,
                    is_iso=True,
                )
            )
            strike_outlay += p.basis_or_strike * p.shares

    year = sale_date.year
    # If only exercises, tax year = exercise year
    if not sales and exercises:
        year = exercise_date.year
    analysis = analyze_sales(
        _profile_for_year(profile, year),
        sales,
        exercises=exercises,
    )
    proceeds = analysis.total_proceeds
    tax = analysis.total_tax
    # Net cash = sale proceeds - tax - strike outlay (held exercises cost cash)
    net = proceeds - tax - strike_outlay
    # For cashless, strike is often netted from proceeds — still a cash cost
    return {
        'analysis': analysis,
        'proceeds': proceeds,
        'tax': tax,
        'strike_outlay': strike_outlay,
        'net_cash': net,
    }


def _picks_from_allocation(
    allocation: List[Tuple[LotSpec, str, float, str, bool, str]],
    price: float,
) -> List[LotPick]:
    picks = []
    for spec, action, shares, reason, is_lt, disp in allocation:
        if shares <= 1e-9:
            continue
        basis = spec.strike_price if spec.is_iso else spec.cost_basis_per_share
        if action == 'sell_rsu':
            gain = (price - basis) * shares
        elif action == 'iso_cashless_dd':
            gain = (price - basis) * shares
        else:
            gain = (price - basis) * shares
        score, _, _, _ = _lot_rank_score(
            spec,
            price=price,
            sale_date=date.today(),
            mode='cashless' if action == 'iso_cashless_dd' else 'sell_held',
        )
        picks.append(
            LotPick(
                vest_event_id=spec.vest_event_id,
                grant_id=spec.grant_id,
                label=spec.label,
                share_type=spec.share_type,
                is_iso=spec.is_iso,
                action=action,
                shares=shares,
                price=price,
                basis_or_strike=basis,
                estimated_gain=gain,
                is_long_term=is_lt,
                iso_disposition=disp,
                rank_score=score,
                reason=reason,
            )
        )
    return picks


# ---------------------------------------------------------------------------
# Core optimizer: hit target net cash, minimize tax
# ---------------------------------------------------------------------------

def optimize_goal(
    profile: dict,
    inventory_lots: Sequence[dict],
    goal: GoalRequest,
) -> GoalPlanResult:
    """
    Main entry: select specific lots/actions to meet goal.target_net_cash
    while minimizing tax (default) or shares.
    """
    try:
        return _optimize_goal_impl(profile, inventory_lots, goal)
    except Exception as e:
        return GoalPlanResult(
            success=False,
            goal=goal.to_dict() if goal else {},
            achieved_net_cash=0,
            shortfall=float(goal.target_net_cash or 0) if goal else 0,
            total_proceeds=0,
            total_tax=0,
            total_strike_outlay=0,
            effective_tax_rate=0,
            picks=[],
            actions_summary=[],
            efficiency_notes=[f'Optimizer error: {e}'],
            warnings=[str(e)],
        )


def _optimize_goal_impl(
    profile: dict,
    inventory_lots: Sequence[dict],
    goal: GoalRequest,
) -> GoalPlanResult:
    price = float(goal.sale_price or 0)
    if price <= 0:
        return GoalPlanResult(
            success=False,
            goal=goal.to_dict(),
            achieved_net_cash=0,
            shortfall=goal.target_net_cash or 0,
            total_proceeds=0,
            total_tax=0,
            total_strike_outlay=0,
            effective_tax_rate=0,
            picks=[],
            actions_summary=[],
            efficiency_notes=[],
            warnings=['Sale/exercise price must be > 0.'],
        )

    sale_date = goal.sale_date or date.today()
    exercise_date = goal.exercise_date or sale_date
    fmv = float(goal.exercise_fmv if goal.exercise_fmv is not None else price)
    target = float(goal.target_net_cash or 0)

    specs = inventory_to_specs(inventory_lots or [], price)

    # Combined strategy: exercise-all ISO (hold) + fund strike/AMT via RSU + pocket net
    wants_ex_all = bool(goal.exercise_all_iso) or (
        goal.allow_iso_exercise_hold
        and goal.iso_prefer_hold_fraction is not None
        and float(goal.iso_prefer_hold_fraction) >= 0.999
    )
    has_unex_iso = any(
        s.is_iso and (s.shares_unexercised or 0) > 0 for s in specs
    )
    if wants_ex_all and has_unex_iso:
        return _optimize_exercise_all_iso_and_pocket(
            profile, specs, goal, sale_date, exercise_date, price, fmv, target
        )

    ranked = _build_ranked_candidates(specs, goal, sale_date, price)

    if not ranked:
        return GoalPlanResult(
            success=False,
            goal=goal.to_dict(),
            achieved_net_cash=0,
            shortfall=target,
            total_proceeds=0,
            total_tax=0,
            total_strike_outlay=0,
            effective_tax_rate=0,
            picks=[],
            actions_summary=[],
            efficiency_notes=[],
            warnings=['No eligible lots for the selected options. Check inventory and allow-flags.'],
        )

    # Greedy fill by tax efficiency, then refine last lot with binary search
    allocation: List[Tuple[LotSpec, str, float, str, bool, str]] = []
    remaining_cap = {id(c[0]) + hash(c[1]): c[2] for c in ranked}

    # Approximate net per share for greedy (before full stack)
    def approx_net_per_share(action: str, spec: LotSpec) -> float:
        if action == 'iso_cashless_dd':
            # net ≈ (price - strike) * (1 - 0.40) roughly after tax on bargain
            spread = max(0.0, price - spec.strike_price)
            return spread * 0.55  # conservative
        if action == 'sell_iso_held':
            if spec.exercise_date:
                disp = classify_iso_disposition(spec.grant_date, spec.exercise_date, sale_date)
                if disp == 'qualifying':
                    return price - (price - spec.strike_price) * 0.30
            return price * 0.55
        # RSU
        gain = max(0.0, price - spec.cost_basis_per_share)
        rate = 0.28 if (sale_date - spec.vest_date).days >= 365 else 0.40
        return price - gain * rate

    # If no target → maximize net (sell everything efficient) or max net objective
    if target <= 0 and goal.objective == 'max_net':
        for spec, action, max_sh, reason, is_lt, disp, score in ranked:
            allocation.append((spec, action, max_sh, reason, is_lt, disp))
        picks = _picks_from_allocation(allocation, price)
        # Fix ISO held exercise dates from specs
        picks = _fix_pick_metadata(picks, specs, sale_date, exercise_date, fmv)
        ev = _evaluate_picks_with_specs(profile, picks, specs, sale_date, exercise_date, price, fmv)
        return _result_from_eval(goal, picks, ev, target, ranked)

    if target <= 0:
        # Default: show recommended order without forcing a sale size
        notes = [
            'No target net cash set — showing tax-efficient sell order (best lots first).',
            'Enter a target (e.g. 500000) to compute exact share quantities.',
        ]
        order_notes = [
            f'{i+1}. {c[0].label} · {c[1]} · up to {c[2]:.2f} sh — {c[3]}'
            for i, c in enumerate(ranked[:15])
        ]
        return GoalPlanResult(
            success=True,
            goal=goal.to_dict(),
            achieved_net_cash=0,
            shortfall=0,
            total_proceeds=0,
            total_tax=0,
            total_strike_outlay=0,
            effective_tax_rate=0,
            picks=[],
            actions_summary=order_notes,
            efficiency_notes=notes,
            warnings=[],
            iso_split={},
        )

    # Fill until approx net >= target
    filled: List[Tuple[LotSpec, str, float, str, bool, str]] = []
    for spec, action, max_sh, reason, is_lt, disp, score in ranked:
        nps = approx_net_per_share(action, spec)
        if nps <= 0:
            continue
        # Current approx net
        cur_net = sum(
            approx_net_per_share(a, s) * sh for s, a, sh, _, _, _ in filled
        )
        if cur_net >= target:
            break
        need = target - cur_net
        take = min(max_sh, need / nps * 1.15)  # small buffer for tax stack
        take = min(take, max_sh)
        if take > 1e-6:
            filled.append((spec, action, take, reason, is_lt, disp))

    if not filled:
        # Force fill largest liquidity
        spec, action, max_sh, reason, is_lt, disp, score = ranked[0]
        filled.append((spec, action, max_sh, reason, is_lt, disp))

    # Refine with full tax engine: scale last pick via binary search
    picks = _fix_pick_metadata(
        _picks_from_allocation(filled, price), specs, sale_date, exercise_date, fmv
    )
    picks = _refine_to_target(
        profile, picks, specs, goal, sale_date, exercise_date, price, fmv, target, ranked
    )

    try:
        ev = _evaluate_picks_with_specs(profile, picks, specs, sale_date, exercise_date, price, fmv)
    except Exception:
        # Fallback: approx net without full tax stack so UI still gets picks
        approx_tax = sum(max(0.0, p.estimated_gain) * 0.30 for p in picks)
        proceeds = sum(p.shares * p.price for p in picks)
        outlay = sum(
            p.basis_or_strike * p.shares for p in picks if p.action == 'iso_cashless_dd'
        )
        class _Ev:
            pass
        ev = {
            'analysis': None,
            'proceeds': proceeds,
            'tax': approx_tax,
            'strike_outlay': outlay,
            'net_cash': proceeds - approx_tax - outlay,
        }

    # ISO split optimization if user wants exercise+hold mix
    iso_split = {}
    if goal.allow_iso_exercise_hold and goal.iso_prefer_hold_fraction is not None:
        try:
            iso_split = _optimize_iso_split(
                profile, specs, goal, sale_date, exercise_date, price, fmv, target
            )
        except Exception:
            iso_split = {}

    # Alternatives
    try:
        alternatives = _build_alternatives(
            profile, specs, goal, sale_date, exercise_date, price, fmv, target, ev
        )
    except Exception:
        alternatives = []

    return _result_from_eval(goal, picks, ev, target, ranked, alternatives, iso_split)


def _optimize_exercise_all_iso_and_pocket(
    profile: dict,
    specs: List[LotSpec],
    goal: GoalRequest,
    sale_date: date,
    exercise_date: date,
    price: float,
    fmv: float,
    target: float,
) -> GoalPlanResult:
    """
    Exercise all unexercised ISOs (hold — no ISO sale), then sell min-tax RSUs so that:

        sale_proceeds − stacked_tax − strike_outlay  ≥  target_net_cash

    Target is **pocket net** after funding strike and ISO AMT (same-year stack).
    """
    from app.utils.shares import whole_shares
    from copy import deepcopy as _dc

    iso_picks = _iso_exercise_hold_picks(specs, price=price, fmv=fmv)
    if not iso_picks:
        return GoalPlanResult(
            success=False,
            goal=goal.to_dict(),
            achieved_net_cash=0,
            shortfall=target,
            total_proceeds=0,
            total_tax=0,
            total_strike_outlay=0,
            effective_tax_rate=0,
            picks=[],
            actions_summary=[],
            efficiency_notes=[],
            warnings=['exercise_all_iso set but no unexercised ISO shares found.'],
        )

    # RSU-only ranking for funding + pocket (no ISO cashless, no selling held ISO for this mode)
    g_rsu = _dc(goal)
    g_rsu.exercise_all_iso = False
    g_rsu.allow_iso_cashless = False
    g_rsu.allow_iso_sell_held = False
    g_rsu.allow_iso_exercise_hold = False
    g_rsu.allow_rsu = True
    ranked = _build_ranked_candidates(specs, g_rsu, sale_date, price)

    # Baseline: exercise only
    base_ev = _evaluate_picks_with_specs(
        profile, iso_picks, specs, sale_date, exercise_date, price, fmv
    )
    base_net = float(base_ev['net_cash'])
    base_strike = float(base_ev['strike_outlay'])
    base_tax = float(base_ev['tax'])

    if not ranked:
        notes = [
            f'Exercise-and-hold all unexercised ISO: strike outlay ${base_strike:,.0f}, '
            f'incremental tax (AMT/etc.) ${base_tax:,.0f}, net cash ${base_net:,.0f}.',
            'No RSU inventory to fund costs or raise pocket cash.',
        ]
        return _result_from_eval(
            goal, iso_picks, base_ev, target, ranked, [], {
                'mode': 'exercise_all_hold',
                'strike_outlay': base_strike,
                'iso_tax': base_tax,
                'iso_shares': sum(p.shares for p in iso_picks),
            }
        )

    # Pocket target: if user set none, still cover costs (pocket ≥ 0) unless max_net
    pocket = target if target > 0 else 0.0
    if target <= 0 and goal.objective == 'max_net':
        # Sell all efficient RSU + exercise all ISO
        rsu_alloc = [(c[0], c[1], c[2], c[3], c[4], c[5]) for c in ranked]
        rsu_picks = _fix_pick_metadata(
            _picks_from_allocation(rsu_alloc, price), specs, sale_date, exercise_date, fmv
        )
        picks = iso_picks + rsu_picks
        ev = _evaluate_picks_with_specs(
            profile, picks, specs, sale_date, exercise_date, price, fmv
        )
        return _result_from_eval(
            goal, picks, ev, 0, ranked, [], _iso_fund_meta(iso_picks, base_ev, ev)
        )

    # Greedy RSU fill until stacked net (with ISO always included) meets pocket target
    rsu_filled: List[Tuple[LotSpec, str, float, str, bool, str]] = []

    def approx_nps(spec: LotSpec) -> float:
        gain = max(0.0, price - spec.cost_basis_per_share)
        rate = 0.28 if (sale_date - spec.vest_date).days >= 365 else 0.42
        return max(0.01, price - gain * rate)

    for spec, action, max_sh, reason, is_lt, disp, score in ranked:
        # Approximate current stacked net
        trial = iso_picks + _fix_pick_metadata(
            _picks_from_allocation(rsu_filled, price), specs, sale_date, exercise_date, fmv
        )
        try:
            cur = _evaluate_picks_with_specs(
                profile, trial, specs, sale_date, exercise_date, price, fmv
            )['net_cash']
        except Exception:
            cur = base_net + sum(
                approx_nps(s) * sh for s, a, sh, _, _, _ in rsu_filled
            )
        if cur >= pocket * 0.995:
            break
        need = pocket - cur
        nps = approx_nps(spec)
        take = min(float(max_sh), max(1.0, need / nps * 1.2))
        take = float(whole_shares(take))
        if take < 1 and max_sh >= 1:
            take = 1.0
        if take > 1e-6:
            rsu_filled.append((spec, action, min(take, float(max_sh)), reason, is_lt, disp))

    if not rsu_filled and pocket > base_net:
        # Force first RSU lot fully into trial
        spec, action, max_sh, reason, is_lt, disp, score = ranked[0]
        rsu_filled.append((spec, action, float(max_sh), reason, is_lt, disp))

    rsu_picks = _fix_pick_metadata(
        _picks_from_allocation(rsu_filled, price), specs, sale_date, exercise_date, fmv
    )
    picks = iso_picks + rsu_picks

    # Refine: only scale RSU portion (keep ISO holds fixed)
    picks = _refine_rsu_with_locked_iso(
        profile,
        picks,
        iso_picks,
        specs,
        goal,
        sale_date,
        exercise_date,
        price,
        fmv,
        pocket,
        ranked,
    )

    try:
        ev = _evaluate_picks_with_specs(
            profile, picks, specs, sale_date, exercise_date, price, fmv
        )
    except Exception as e:
        return GoalPlanResult(
            success=False,
            goal=goal.to_dict(),
            achieved_net_cash=0,
            shortfall=pocket,
            total_proceeds=0,
            total_tax=0,
            total_strike_outlay=base_strike,
            effective_tax_rate=0,
            picks=picks,
            actions_summary=[],
            efficiency_notes=[f'Exercise+fund evaluation failed: {e}'],
            warnings=[str(e)],
            iso_split=_iso_fund_meta(iso_picks, base_ev, base_ev),
        )

    alts = [
        {
            'name': 'Pocket only (no ISO exercise)',
            'note': 'Drop exercise_all_iso to raise cash without starting the ISO QD clock or paying strike/AMT.',
        },
        {
            'name': 'Cashless ISO instead of hold',
            'note': 'Selling ISO same-day is DD (ordinary on bargain) — usually worse tax than RSU LT + hold ISO.',
        },
    ]
    return _result_from_eval(
        goal, picks, ev, pocket, ranked, alts, _iso_fund_meta(iso_picks, base_ev, ev)
    )


def _iso_fund_meta(
    iso_picks: List[LotPick],
    base_ev: Dict[str, Any],
    full_ev: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        'mode': 'exercise_all_hold_fund_rsu',
        'iso_shares': sum(p.shares for p in iso_picks),
        'iso_lots': len(iso_picks),
        'strike_outlay': float(base_ev.get('strike_outlay') or 0),
        'iso_only_tax': float(base_ev.get('tax') or 0),
        'iso_only_net': float(base_ev.get('net_cash') or 0),
        'combined_tax': float(full_ev.get('tax') or 0),
        'combined_net': float(full_ev.get('net_cash') or 0),
        'rsu_gross': float(full_ev.get('proceeds') or 0),
    }


def _refine_rsu_with_locked_iso(
    profile: dict,
    picks: List[LotPick],
    iso_picks: List[LotPick],
    specs: Sequence[LotSpec],
    goal: GoalRequest,
    sale_date: date,
    exercise_date: date,
    price: float,
    fmv: float,
    target: float,
    ranked,
) -> List[LotPick]:
    """Like _refine_to_target but never changes iso_exercise_hold rows."""
    from app.utils.shares import whole_shares

    iso_ids = {(p.vest_event_id, p.action) for p in iso_picks}
    rsu_picks = [p for p in picks if (p.vest_event_id, p.action) not in iso_ids]

    def combine(rsu: List[LotPick]) -> List[LotPick]:
        return list(iso_picks) + [p for p in rsu if p.shares > 0]

    # Add RSU capacity until target met
    for _ in range(12):
        ev = _evaluate_picks_with_specs(
            profile, combine(rsu_picks), specs, sale_date, exercise_date, price, fmv
        )
        if ev['net_cash'] >= target * 0.995:
            break
        used = {(p.vest_event_id, p.action): p.shares for p in rsu_picks}
        added = False
        for spec, action, max_sh, reason, is_lt, disp, score in ranked:
            key = (spec.vest_event_id, action)
            already = used.get(key, 0.0)
            if already >= max_sh - 1e-6:
                continue
            room = float(whole_shares(max_sh - already))
            if room < 1:
                continue
            add = max(1.0, min(room, float(whole_shares(max(room * 0.25, 1)))))
            if already > 0:
                for p in rsu_picks:
                    if p.vest_event_id == spec.vest_event_id and p.action == action:
                        p.shares = float(whole_shares(min(max_sh, p.shares + add)))
                        break
            else:
                rsu_picks.append(
                    LotPick(
                        vest_event_id=spec.vest_event_id,
                        grant_id=spec.grant_id,
                        label=spec.label,
                        share_type=spec.share_type,
                        is_iso=False,
                        action=action,
                        shares=add,
                        price=price,
                        basis_or_strike=spec.cost_basis_per_share,
                        estimated_gain=0,
                        is_long_term=is_lt,
                        iso_disposition=disp,
                        rank_score=score,
                        reason=reason,
                    )
                )
            added = True
            break
        if not added:
            break

    # Binary trim last RSU lot (whole shares) if overshooting
    for _ in range(24):
        if not rsu_picks:
            break
        ev = _evaluate_picks_with_specs(
            profile, combine(rsu_picks), specs, sale_date, exercise_date, price, fmv
        )
        if ev['net_cash'] < target:
            break
        last = rsu_picks[-1]
        if last.shares <= 1:
            # Try drop last lot only if still meeting without it
            trial = rsu_picks[:-1]
            e2 = _evaluate_picks_with_specs(
                profile, combine(trial), specs, sale_date, exercise_date, price, fmv
            )
            if e2['net_cash'] >= target * 0.995:
                rsu_picks.pop()
                continue
            break
        lo, hi = 1, int(last.shares)
        best_sh = int(last.shares)
        while lo <= hi:
            mid = (lo + hi) // 2
            last.shares = float(mid)
            e2 = _evaluate_picks_with_specs(
                profile, combine(rsu_picks), specs, sale_date, exercise_date, price, fmv
            )
            if e2['net_cash'] >= target * 0.995:
                best_sh = mid
                hi = mid - 1
            else:
                lo = mid + 1
        last.shares = float(best_sh)
        break

    rsu_picks = [p for p in rsu_picks if p.shares >= 1]

    # Close shortfalls one whole share at a time until pocket target fully met
    for _ in range(800):
        ev = _evaluate_picks_with_specs(
            profile, combine(rsu_picks), specs, sale_date, exercise_date, price, fmv
        )
        if ev['net_cash'] >= target:
            break
        used = {(p.vest_event_id, p.action): p.shares for p in rsu_picks}
        grew = False
        for spec, action, max_sh, reason, is_lt, disp, score in ranked:
            key = (spec.vest_event_id, action)
            already = used.get(key, 0.0)
            if already >= max_sh - 0.5:
                continue
            if already > 0:
                for p in rsu_picks:
                    if p.vest_event_id == spec.vest_event_id and p.action == action:
                        p.shares = float(whole_shares(min(max_sh, p.shares + 1)))
                        grew = True
                        break
            else:
                rsu_picks.append(
                    LotPick(
                        vest_event_id=spec.vest_event_id,
                        grant_id=spec.grant_id,
                        label=spec.label,
                        share_type=spec.share_type,
                        is_iso=False,
                        action=action,
                        shares=1.0,
                        price=price,
                        basis_or_strike=spec.cost_basis_per_share,
                        estimated_gain=0,
                        is_long_term=is_lt,
                        iso_disposition=disp,
                        rank_score=score,
                        reason=reason,
                    )
                )
                grew = True
            if grew:
                break
        if not grew:
            break

    rsu_picks = [p for p in rsu_picks if p.shares >= 1]
    return combine(rsu_picks)


def _fix_pick_metadata(
    picks: List[LotPick],
    specs: Sequence[LotSpec],
    sale_date: date,
    exercise_date: date,
    fmv: float,
) -> List[LotPick]:
    by_id = {s.vest_event_id: s for s in specs}
    out = []
    for p in picks:
        s = by_id.get(p.vest_event_id)
        if s and p.action == 'sell_iso_held' and s.exercise_date:
            disp = classify_iso_disposition(s.grant_date, s.exercise_date, sale_date)
            p.iso_disposition = disp
            p.is_long_term = disp == 'qualifying' or (
                (sale_date - s.exercise_date).days >= 365
            )
        out.append(p)
    return out


def _evaluate_picks_with_specs(
    profile: dict,
    picks: List[LotPick],
    specs: Sequence[LotSpec],
    sale_date: date,
    exercise_date: date,
    price: float,
    fmv: float,
) -> Dict[str, Any]:
    """Evaluate with correct grant/vest/exercise dates from specs."""
    by_id = {s.vest_event_id: s for s in specs}
    sales: List[LotSaleInput] = []
    exercises: List[ExerciseInput] = []
    strike_outlay = 0.0

    for p in picks:
        if p.shares <= 1e-9:
            continue
        s = by_id.get(p.vest_event_id)
        if not s:
            continue
        if p.action == 'sell_rsu':
            sales.append(
                LotSaleInput(
                    vest_event_id=s.vest_event_id,
                    grant_id=s.grant_id,
                    share_type=s.share_type,
                    grant_type=s.grant_type,
                    shares=p.shares,
                    sale_price=price,
                    sale_date=sale_date,
                    vest_date=s.vest_date,
                    grant_date=s.grant_date,
                    cost_basis_per_share=s.cost_basis_per_share,
                    is_iso=False,
                    label=s.label,
                )
            )
        elif p.action == 'sell_iso_held':
            sales.append(
                LotSaleInput(
                    vest_event_id=s.vest_event_id,
                    grant_id=s.grant_id,
                    share_type=s.share_type,
                    grant_type=s.grant_type,
                    shares=p.shares,
                    sale_price=price,
                    sale_date=sale_date,
                    vest_date=s.vest_date,
                    grant_date=s.grant_date,
                    cost_basis_per_share=s.strike_price,
                    is_iso=True,
                    strike_price=s.strike_price,
                    exercise_date=s.exercise_date,
                    fmv_at_exercise=s.fmv_at_exercise if s.fmv_at_exercise is not None else fmv,
                    label=s.label,
                )
            )
        elif p.action == 'iso_cashless_dd':
            sales.append(
                LotSaleInput(
                    vest_event_id=s.vest_event_id,
                    grant_id=s.grant_id,
                    share_type=s.share_type,
                    grant_type=s.grant_type,
                    shares=p.shares,
                    sale_price=price,
                    sale_date=sale_date,
                    vest_date=s.vest_date,
                    grant_date=s.grant_date,
                    cost_basis_per_share=s.strike_price,
                    is_iso=True,
                    strike_price=s.strike_price,
                    exercise_date=exercise_date,
                    fmv_at_exercise=fmv,
                    label=s.label,
                )
            )
            strike_outlay += s.strike_price * p.shares
        elif p.action == 'iso_exercise_hold':
            exercises.append(
                ExerciseInput(
                    vest_event_id=s.vest_event_id,
                    shares=p.shares,
                    exercise_date=exercise_date,
                    strike_price=s.strike_price,
                    fmv_at_exercise=fmv,
                    grant_date=s.grant_date,
                    label=s.label,
                    is_iso=True,
                )
            )
            strike_outlay += s.strike_price * p.shares

    year = sale_date.year if sales else exercise_date.year
    analysis = analyze_sales(
        _profile_for_year(profile, year),
        sales,
        exercises=exercises,
    )
    proceeds = analysis.total_proceeds
    tax = analysis.total_tax
    net = proceeds - tax - strike_outlay
    return {
        'analysis': analysis,
        'proceeds': proceeds,
        'tax': tax,
        'strike_outlay': strike_outlay,
        'net_cash': net,
    }


def _refine_to_target(
    profile: dict,
    picks: List[LotPick],
    specs: Sequence[LotSpec],
    goal: GoalRequest,
    sale_date: date,
    exercise_date: date,
    price: float,
    fmv: float,
    target: float,
    ranked,
) -> List[LotPick]:
    """Binary-search scale on cumulative picks to meet target with min shares/tax."""
    if not picks:
        return picks

    # If under target, add more from ranked
    for _ in range(8):
        ev = _evaluate_picks_with_specs(profile, picks, specs, sale_date, exercise_date, price, fmv)
        if ev['net_cash'] >= target * 0.995:
            break
        # Add next unused capacity
        used = {(p.vest_event_id, p.action): p.shares for p in picks}
        added = False
        for spec, action, max_sh, reason, is_lt, disp, score in ranked:
            key = (spec.vest_event_id, action)
            already = used.get(key, 0.0)
            if already >= max_sh - 1e-6:
                continue
            add = min(max_sh - already, max_sh * 0.25 + 1)
            if already > 0:
                for p in picks:
                    if p.vest_event_id == spec.vest_event_id and p.action == action:
                        p.shares = min(max_sh, p.shares + add)
                        break
            else:
                picks.append(
                    LotPick(
                        vest_event_id=spec.vest_event_id,
                        grant_id=spec.grant_id,
                        label=spec.label,
                        share_type=spec.share_type,
                        is_iso=spec.is_iso,
                        action=action,
                        shares=add,
                        price=price,
                        basis_or_strike=spec.strike_price if spec.is_iso else spec.cost_basis_per_share,
                        estimated_gain=0,
                        is_long_term=is_lt,
                        iso_disposition=disp,
                        rank_score=score,
                        reason=reason,
                    )
                )
            added = True
            break
        if not added:
            break

    # Trim last pick if over-shoot (min shares / min excess)
    for _ in range(20):
        ev = _evaluate_picks_with_specs(profile, picks, specs, sale_date, exercise_date, price, fmv)
        if ev['net_cash'] < target:
            break
        if not picks:
            break
        last = picks[-1]
        if last.shares <= 0.01:
            picks.pop()
            continue
        # Try reduce last pick
        lo, hi = 0.0, last.shares
        best_sh = last.shares
        for _ in range(16):
            mid = (lo + hi) / 2
            last.shares = mid
            e2 = _evaluate_picks_with_specs(profile, picks, specs, sale_date, exercise_date, price, fmv)
            if e2['net_cash'] >= target:
                best_sh = mid
                hi = mid
            else:
                lo = mid
        last.shares = best_sh
        break

    picks = [p for p in picks if p.shares > 1e-6]
    return picks


def _optimize_iso_split(
    profile: dict,
    specs: Sequence[LotSpec],
    goal: GoalRequest,
    sale_date: date,
    exercise_date: date,
    price: float,
    fmv: float,
    target: float,
) -> Dict[str, Any]:
    """Search sell vs hold fractions for unexercised ISOs."""
    unex = [s for s in specs if s.is_iso and (s.shares_unexercised or 0) > 0]
    if not unex:
        return {}
    total = sum(s.shares_unexercised for s in unex)
    hold_frac = goal.iso_prefer_hold_fraction if goal.iso_prefer_hold_fraction is not None else 0.5
    results = []
    for frac_sell in [0.0, 0.25, 0.5, 0.75, 1.0, hold_frac, 1 - hold_frac]:
        frac_sell = max(0.0, min(1.0, frac_sell))
        sell_sh = total * frac_sell
        hold_sh = total - sell_sh
        # Build picks
        picks = []
        rem_s, rem_h = sell_sh, hold_sh
        for s in unex:
            u = s.shares_unexercised
            ts = min(u, rem_s)
            rem_s -= ts
            th = min(u - ts, rem_h)
            rem_h -= th
            if ts > 0:
                picks.append(
                    LotPick(
                        vest_event_id=s.vest_event_id,
                        grant_id=s.grant_id,
                        label=s.label,
                        share_type=s.share_type,
                        is_iso=True,
                        action='iso_cashless_dd',
                        shares=ts,
                        price=price,
                        basis_or_strike=s.strike_price,
                        estimated_gain=(price - s.strike_price) * ts,
                        is_long_term=False,
                        iso_disposition='disqualifying',
                        rank_score=0,
                        reason='ISO split search — cashless',
                    )
                )
            if th > 0:
                picks.append(
                    LotPick(
                        vest_event_id=s.vest_event_id,
                        grant_id=s.grant_id,
                        label=s.label,
                        share_type=s.share_type,
                        is_iso=True,
                        action='iso_exercise_hold',
                        shares=th,
                        price=fmv,
                        basis_or_strike=s.strike_price,
                        estimated_gain=(fmv - s.strike_price) * th,
                        is_long_term=False,
                        iso_disposition='hold',
                        rank_score=0,
                        reason='ISO split search — hold for QD path',
                    )
                )
        ev = _evaluate_picks_with_specs(profile, picks, specs, sale_date, exercise_date, price, fmv)
        results.append({
            'frac_sell': frac_sell,
            'shares_sell': sell_sh,
            'shares_hold': hold_sh,
            'net_cash': ev['net_cash'],
            'tax': ev['tax'],
            'strike_outlay': ev['strike_outlay'],
        })
    # Best for min tax among those meeting target, else max net
    meeting = [r for r in results if r['net_cash'] >= target * 0.99] if target > 0 else results
    if meeting:
        best = min(meeting, key=lambda r: r['tax'])
    else:
        best = max(results, key=lambda r: r['net_cash'])
    return {'candidates': results, 'recommended': best}


def _build_alternatives(
    profile, specs, goal, sale_date, exercise_date, price, fmv, target, primary_ev
) -> List[Dict[str, Any]]:
    alts = []
    # Alt 1: RSU + held ISO only (no cashless)
    g2 = deepcopy(goal)
    g2.allow_iso_cashless = False
    # cheap: just note
    alts.append({
        'name': 'Prefer LTCG only (no ISO cashless)',
        'note': 'Turn off ISO cashless in goal options for pure LTCG-path lots. Lower tax if enough RSU/held inventory.',
    })
    alts.append({
        'name': 'ISO exercise & hold remainder',
        'note': 'After funding net cash via efficient sales, exercise remaining ISOs to start the QD clock (costs strike + AMT).',
    })
    alts.append({
        'name': 'Sell-to-cover',
        'note': 'Use strategy iso_sell_to_cover in the scenario planner to fund AMT/strike without full cashless.',
    })
    return alts


def _result_from_eval(
    goal: GoalRequest,
    picks: List[LotPick],
    ev: Dict[str, Any],
    target: float,
    ranked,
    alternatives: Optional[List] = None,
    iso_split: Optional[Dict] = None,
) -> GoalPlanResult:
    analysis = ev.get('analysis')
    net = float(ev.get('net_cash') or 0)
    tax = float(ev.get('tax') or 0)
    proceeds = float(ev.get('proceeds') or 0)
    outlay = float(ev.get('strike_outlay') or 0)
    # Effective rate must use economic gain (proceeds − basis + equity ordinary),
    # NOT gross proceeds — using max(proceeds, gain) understated rates badly.
    if analysis is not None:
        try:
            gain = (
                float(analysis.total_proceeds)
                - float(analysis.total_cost_basis)
                + float(analysis.equity_ordinary)
            )
            if gain <= 0 and float(getattr(analysis, 'amt_due', 0) or 0) > 0:
                gain = float(getattr(analysis, 'equity_ordinary', 0) or 0)
        except Exception:
            gain = sum(max(0.0, p.estimated_gain) for p in picks)
    else:
        gain = sum(max(0.0, p.estimated_gain) for p in picks)
    if gain <= 0:
        gain = max(0.0, proceeds - outlay)
    eff = (tax / gain) if gain > 0 else 0.0

    summary = []
    for p in picks:
        summary.append(
            f"{p.action}: {p.shares:,.2f} sh of {p.label} @ ${p.price:.2f} — {p.reason}"
        )

    notes = [
        'Lots ordered for tax efficiency: LTCG / high-basis first; ISO cashless (ordinary) last.',
        'RSU cost basis = FMV on vest date (from your Prices history), not $0 — gain is (sale − vest FMV).',
        'Tax is incremental vs Tax Profile wages only (upcoming unvested RSU income is not auto-included).',
        'Eff. rate = incremental tax ÷ economic gain (proceeds − basis), not ÷ gross proceeds.',
        'This is SpecID planning — execute the same vest lots when selling.',
    ]
    zero_basis_rsu = [
        p for p in picks
        if p.action == 'sell_rsu' and float(p.basis_or_strike or 0) <= 0.01
    ]
    if zero_basis_rsu:
        notes.insert(
            0,
            'WARNING: one or more RSU lots have ~$0 cost basis — usually missing pre-IPO '
            'price history on the vest date. Check Prices for a valuation on/before each vest; '
            'gain (and tax) may be overstated until fixed.',
        )
    iso_hold_n = sum(1 for p in picks if p.action == 'iso_exercise_hold')
    if iso_hold_n:
        notes.insert(
            0,
            f'ISO exercise-and-hold included ({iso_hold_n} lot(s)): strike outlay '
            f'${outlay:,.0f} is deducted from pocket net; AMT/tax is stacked with RSU sales '
            f'in one engine run. Achieved net = proceeds − tax − strike.',
        )
        if iso_split and iso_split.get('mode') == 'exercise_all_hold_fund_rsu':
            notes.insert(
                1,
                f"ISO-only baseline: tax ${float(iso_split.get('iso_only_tax') or 0):,.0f}, "
                f"net ${float(iso_split.get('iso_only_net') or 0):,.0f}. "
                f"RSU sales fund that cost plus your pocket target.",
            )
    if analysis is not None:
        try:
            rates = getattr(analysis, 'rates_used', None) or {}
            if isinstance(analysis, dict):
                rates = analysis.get('rates_used') or {}
                other_ord = float(analysis.get('other_ordinary') or 0)
                ltcg_r = rates.get('ltcg')
            else:
                other_ord = float(getattr(analysis, 'other_ordinary', 0) or 0)
                ltcg_r = rates.get('ltcg') if isinstance(rates, dict) else None
            if ltcg_r is not None:
                notes.append(
                    f'Assumptions: profile ordinary ${other_ord:,.0f} · federal LTCG rate used {float(ltcg_r)*100:.0f}% '
                    f'(20% band starts ~$545k taxable income single 2026).'
                )
        except Exception:
            pass
    if target > 0:
        if net >= target * 0.995:
            notes.append(f'Target ${target:,.0f} net cash met (achieved ${net:,.0f}).')
        else:
            notes.append(
                f'Shortfall ${target - net:,.0f}: not enough efficient inventory at this price, '
                f'or allow ISO cashless / higher price.'
            )

    success = (target <= 0) or (net >= target * 0.99)

    tax_analysis_dict = None
    if analysis is not None:
        try:
            tax_analysis_dict = analysis.to_dict()
        except Exception:
            tax_analysis_dict = None

    warnings: List[str] = []
    if analysis is not None:
        try:
            warnings = list(analysis.warnings or [])
        except Exception:
            warnings = []

    return GoalPlanResult(
        success=success,
        goal=goal.to_dict(),
        achieved_net_cash=net,
        shortfall=max(0.0, target - net) if target > 0 else 0.0,
        total_proceeds=proceeds,
        total_tax=tax,
        total_strike_outlay=outlay,
        effective_tax_rate=eff,
        picks=picks,
        actions_summary=summary,
        efficiency_notes=notes,
        tax_analysis=tax_analysis_dict,
        alternatives=alternatives or [],
        warnings=warnings,
        iso_split=iso_split or {},
    )


def parse_goal_heuristic(text: str, defaults: Optional[dict] = None) -> GoalRequest:
    """Regex/heuristic NL parse when Grok is unavailable."""
    import re
    defaults = defaults or {}
    g = GoalRequest(
        sale_price=float(defaults.get('sale_price') or 0),
        sale_date=defaults.get('sale_date'),
        exercise_date=defaults.get('exercise_date'),
        exercise_fmv=defaults.get('exercise_fmv'),
        raw_text=text or '',
    )
    t = (text or '').lower()
    # money targets: "net 50k", "get 300k liquid", "$50,000", "50k cash"
    money_patterns = [
        r'(?:net|after[\s-]?tax|take[\s-]?home|liquid(?:ity)?|cash(?:\s+out)?|'
        r'need|want|get|raise|have)\s*\$?\s*([\d,]+(?:\.\d+)?)\s*([kmb])?\b',
        r'\$\s*([\d,]+(?:\.\d+)?)\s*([kmb])?',
        r'\b([\d,]+(?:\.\d+)?)\s*([kmb])\b\s*(?:liquid|cash|net|after)?',
    ]
    for pat in money_patterns:
        m = re.search(pat, t)
        if not m:
            continue
        try:
            val = float(m.group(1).replace(',', ''))
        except (ValueError, IndexError):
            continue
        suf = (m.group(2) or '').lower() if m.lastindex and m.lastindex >= 2 else ''
        if suf == 'k':
            val *= 1_000
        elif suf == 'm':
            val *= 1_000_000
        elif suf == 'b':
            val *= 1_000_000_000
        if not suf and val < 1000:
            continue
        g.target_net_cash = val
        break

    if 'minimi' in t and 'share' in t:
        g.objective = 'min_shares'
    elif 'max' in t and 'net' in t:
        g.objective = 'max_net'
    else:
        g.objective = 'min_tax'

    if 'no cashless' in t or 'no iso sale' in t:
        g.allow_iso_cashless = False
    if 'hold iso' in t or 'exercise and hold' in t or 'exercise & hold' in t:
        g.allow_iso_exercise_hold = True
        g.iso_prefer_hold_fraction = 0.5
    if 'only rsu' in t:
        g.allow_iso_cashless = False
        g.allow_iso_sell_held = False

    # Exercise-all ISO (hold) + fund costs via RSU sales + optional pocket cash
    wants_exercise_all = bool(
        re.search(
            r'exercise\s+(all|every|my\s+iso|all\s+my\s+iso|the\s+iso|isos?\b)',
            t,
        )
        or re.search(r"don'?t\s+sell\s+just\s+exercise|do\s+not\s+sell.*exercise", t)
        or re.search(r'exercise.*(?:hold|no\s+sell|without\s+sell)', t)
    )
    wants_fund = bool(
        re.search(
            r'cover\s+(the\s+)?(cost|strike|tax|amt|taxes)|'
            r'fund\s+(the\s+)?(iso|exercise|strike|amt|tax)|'
            r'via\s+selling\s+rsu|sell\s+rsus?\s+to\s+(cover|fund|pay)|'
            r'pay\s+for\s+(the\s+)?(exercise|iso|strike)',
            t,
        )
    )
    if wants_exercise_all or (wants_fund and 'iso' in t):
        g.exercise_all_iso = True
        g.allow_iso_exercise_hold = True
        g.allow_iso_cashless = False  # hold path — do not DD-sell ISOs
        g.iso_prefer_hold_fraction = 1.0
        g.allow_rsu = True

    return g
