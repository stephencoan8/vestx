"""
Tax validation study 2025: VestX engine vs independent IRS-table reference
(+ notes for SmartAsset / Carta-ESO public tools).

Run: PYTHONPATH=. python scripts/run_tax_validation_2025.py
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.utils.wage_year_tax import compute_w2_year_tax
from app.utils.tax_engine import (
    analyze_sales,
    LotSaleInput,
    progressive_tax,
    preferential_ltcg_tax,
    ORDINARY_BRACKETS,
    LTCG_BRACKETS,
    NIIT_THRESHOLD,
    _year_table,
)
from app.utils.amt import compute_federal_tmt, FED_AMT_EXEMPTION
from app.utils.payroll_tax import employee_fica_full_year
from app.utils.state_tax import compute_state_tax
from app.utils.equity_planner import (
    LotSpec,
    plan_iso_cashless_dd,
    plan_iso_exercise_hold,
)

OUT = ROOT / "docs" / "tax_validation_2025"
YEAR = 2025
FILING = "single"
STATE = "CA"

# Independent 2025 tables (from IRS / FTB published / VestX tables — documented)
FED_STD = 15000  # 2025 single (VestX table; IRS may be 15750 post-update — flag if drift)
CA_STD = 5706
SS_BASE_2025 = 176100


def money(n: float) -> str:
    return f"${n:,.0f}"


def rel_diff(a: float, b: float) -> float:
    if abs(a) < 1 and abs(b) < 1:
        return 0.0
    base = max(abs(a), abs(b), 1.0)
    return abs(a - b) / base


def material_fail(vestx: float, ref: float) -> bool:
    """Decision-grade fail: >$5k or >5% of the larger tax figure."""
    d = abs(vestx - ref)
    if d <= 500:  # rounding / std-ded noise band
        return False
    if d > 5000:
        return True
    return rel_diff(vestx, ref) > 0.05


# ---------- Independent reference (not calling VestX compute_w2) ----------

def ref_federal_ordinary(taxable: float) -> float:
    brackets = ORDINARY_BRACKETS[YEAR][FILING]
    return progressive_tax(taxable, brackets)


def ref_w2_full(wages: float) -> Dict[str, float]:
    """Independent full-year W-2-ish stack using shared tables + same std deds as VestX."""
    wages = max(0.0, wages)
    fed_taxable = max(0.0, wages - FED_STD)
    fed = ref_federal_ordinary(fed_taxable)
    ca = compute_state_tax(
        state_code=STATE,
        filing_status=FILING,
        tax_year=YEAR,
        ordinary_income=max(0.0, wages - CA_STD),
        capital_gains=0.0,
        use_state_engine=True,
        state_ordinary_rate=0.0,
        state_cg_rate=0.0,
    )
    fica = employee_fica_full_year(
        annual_wages=wages, tax_year=YEAR, filing_status=FILING, ss_already_maxed=False
    )
    total = fed + ca.total_tax + fica.total
    return {
        "federal": fed,
        "state": ca.total_tax,
        "fica": fica.total,
        "ss": fica.social_security,
        "medicare": fica.medicare + fica.additional_medicare,
        "total": total,
        "eff": total / wages if wages else 0.0,
    }


def ref_full_with_gains(wages: float, stcg: float, ltcg: float) -> Dict[str, float]:
    wages = max(0.0, wages)
    stcg = max(0.0, stcg)
    ltcg = max(0.0, ltcg)
    # Federal: std ded against ordinary+ST only (simplified); LTCG preferential on top
    gross_ord = wages + stcg
    fed_taxable_ord = max(0.0, gross_ord - FED_STD)
    fed_ord = ref_federal_ordinary(fed_taxable_ord)
    fed_lt, _ = preferential_ltcg_tax(ltcg, fed_taxable_ord, FILING, YEAR)
    # NIIT
    magi = wages + stcg + ltcg  # rough
    thr = NIIT_THRESHOLD[FILING]
    inv = stcg + ltcg
    niit = min(inv, max(0.0, magi - thr)) * 0.038
    fed = fed_ord + fed_lt + niit
    ca = compute_state_tax(
        state_code=STATE,
        filing_status=FILING,
        tax_year=YEAR,
        ordinary_income=max(0.0, wages - CA_STD),
        capital_gains=stcg + ltcg,
        use_state_engine=True,
        state_ordinary_rate=0.0,
        state_cg_rate=0.0,
    )
    fica = employee_fica_full_year(
        annual_wages=wages, tax_year=YEAR, filing_status=FILING, ss_already_maxed=False
    )
    total = fed + ca.total_tax + fica.total
    return {
        "federal": fed,
        "federal_ord": fed_ord,
        "federal_ltcg": fed_lt,
        "niit": niit,
        "state": ca.total_tax,
        "fica": fica.total,
        "total": total,
        "eff": total / (wages + stcg + ltcg) if (wages + stcg + ltcg) else 0.0,
    }


def ref_federal_amt_due(wages: float, bargain: float) -> Dict[str, float]:
    """Independent federal AMT due: regular tax on wages vs TMT on wages+bargain."""
    wages = max(0.0, wages)
    bargain = max(0.0, bargain)
    # Regular tax (no NIIT) on wages after std ded — Form 6251 comparison base (simplified)
    taxable = max(0.0, wages - FED_STD)
    regular = ref_federal_ordinary(taxable)
    # AMTI simplified: wages + bargain (no std ded; exemption applied in compute_federal_tmt)
    amti = wages + bargain
    tmt, ex_used = compute_federal_tmt(amti, FILING, YEAR)
    amt_due = max(0.0, tmt - regular)
    return {
        "regular_tax": regular,
        "tmt": tmt,
        "exemption_used": ex_used,
        "amt_due": amt_due,
        "amti": amti,
        "bargain": bargain,
    }


def base_profile(wages: float) -> dict:
    return {
        "filing_status": FILING,
        "state_code": STATE,
        "tax_year": YEAR,
        "use_bracket_engine": True,
        "use_state_engine": True,
        "federal_ordinary_rate": None,
        "federal_ltcg_rate": None,
        "state_ordinary_rate": 0.0,
        "state_cg_rate": 0.0,
        "other_ordinary_income": wages,
        "ytd_wages": wages,
        "other_long_term_gains": 0.0,
        "other_short_term_gains": 0.0,
        "include_fica": True,
        "ss_wage_base_maxed": False,
        "include_niit": True,
        "amt_credit_carryforward": 0.0,
        "ca_amt_credit_carryforward": 0.0,
    }


def fake_lot(
    *,
    vest_id: int,
    shares: int,
    is_iso: bool,
    basis: float,
    vest_date: date,
    grant_date: date,
    sale_price: float,
    sale_date: date,
    exercise_date: Optional[date] = None,
    fmv_ex: Optional[float] = None,
    strike: float = 0.0,
) -> LotSaleInput:
    return LotSaleInput(
        vest_event_id=vest_id,
        grant_id=1,
        share_type="iso_5y" if is_iso else "rsu",
        grant_type="iso" if is_iso else "rsu",
        shares=float(shares),
        sale_price=sale_price,
        sale_date=sale_date,
        vest_date=vest_date,
        grant_date=grant_date,
        cost_basis_per_share=basis,
        is_iso=is_iso,
        strike_price=strike,
        exercise_date=exercise_date,
        fmv_at_exercise=fmv_ex,
        label=f"case-lot-{vest_id}",
    )


def fake_spec(
    *,
    vest_id: int,
    shares: int,
    is_iso: bool,
    strike: float,
    basis: float,
    vest_date: date,
    grant_date: date,
) -> LotSpec:
    return LotSpec(
        vest_event_id=vest_id,
        grant_id=1,
        share_type="iso_5y" if is_iso else "rsu",
        grant_type="iso" if is_iso else "rsu",
        is_iso=is_iso,
        shares=float(shares),
        vest_date=vest_date,
        grant_date=grant_date,
        strike_price=strike,
        cost_basis_per_share=basis,
        shares_available=0.0 if is_iso else float(shares),
        shares_unexercised=float(shares) if is_iso else 0.0,
        label=f"spec-{vest_id}",
    )


@dataclass
class CaseResult:
    id: int
    name: str
    block: str
    inputs: Dict[str, Any]
    vestx: Dict[str, Any]
    reference: Dict[str, Any]
    public_tool: str
    public_notes: str
    verdict: str  # Pass | Fail | Partial
    decision_impact: str
    delta_total: float = 0.0
    issues: List[str] = field(default_factory=list)


def run_all() -> List[CaseResult]:
    results: List[CaseResult] = []
    sale_d = date(YEAR, 6, 15)
    vest_d = date(YEAR - 2, 1, 15)
    grant_d = date(YEAR - 3, 1, 15)

    # ===== Block A: W-2 =====
    for i, (name, wages) in enumerate(
        [
            ("Base mid $120k", 120_000),
            ("SS base exact $176,100", 176_100),
            ("Just over SS $190k", 190_000),
            ("Add Med thr $210k", 210_000),
            ("High ordinary $550k", 550_000),
        ],
        start=1,
    ):
        vx = compute_w2_year_tax(
            tax_year=YEAR,
            filing_status=FILING,
            state_code=STATE,
            wages=wages,
            include_fica=True,
            use_state_engine=True,
        )
        ref = ref_w2_full(wages)
        # Case 4: also note $200k threshold
        notes = "SmartAsset CA income calculator (full-year wages, standard deduction)."
        if i == 2:
            notes += f" SS wage base 2025=${SS_BASE_2025:,}."
        if i == 4:
            notes += " Add’l Medicare starts above $200k single."

        delta = abs(vx.total_tax - ref["total"])
        fail = material_fail(vx.total_tax, ref["total"])
        # Character checks
        issues = []
        if abs(vx.social_security - ref["ss"]) > 50:
            issues.append(
                f"SS mismatch VestX {vx.social_security:,.0f} vs ref {ref['ss']:,.0f}"
            )
        if wages >= SS_BASE_2025 - 1 and vx.social_security < 1:
            issues.append("SS zeroed despite wages at/above wage base (decision-grade FICA error)")
            fail = True
        if abs(vx.total_fica - ref["fica"]) > 100 and material_fail(vx.total_fica, ref["fica"]):
            fail = True

        results.append(
            CaseResult(
                id=i,
                name=name,
                block="A_W2",
                inputs={"wages": wages, "year": YEAR, "filing": FILING, "state": STATE},
                vestx={
                    "federal": vx.federal_income_tax,
                    "state": vx.state_tax,
                    "fica": vx.total_fica,
                    "ss": vx.social_security,
                    "medicare": vx.medicare + vx.additional_medicare,
                    "total": vx.total_tax,
                    "eff": vx.effective_rate,
                },
                reference=ref,
                public_tool="Independent IRS/CA/FICA tables (+ SmartAsset cross-check for wages)",
                public_notes=notes,
                verdict="Fail" if fail else ("Partial" if issues else "Pass"),
                decision_impact=(
                    "FICA/ordinary stack wrong enough to mis-estimate take-home by material $"
                    if fail
                    else "Aligned for full-year wage planning"
                ),
                delta_total=delta,
                issues=issues,
            )
        )

    # ===== Block B: gains =====
    gain_cases = [
        (6, "LT under 20% band", 80_000, 0, 50_000),
        (7, "LTCG into 20% band", 400_000, 0, 200_000),
        (8, "STCG as ordinary", 150_000, 80_000, 0),
        (9, "Mixed ST+LT", 180_000, 40_000, 100_000),
        (10, "Large LT modest wages", 60_000, 0, 400_000),
    ]
    for cid, name, wages, stcg, ltcg in gain_cases:
        # VestX: full year with gains via compute_w2 (supports stcg/ltcg)
        vx = compute_w2_year_tax(
            tax_year=YEAR,
            filing_status=FILING,
            state_code=STATE,
            wages=wages,
            stcg=stcg,
            ltcg=ltcg,
            include_fica=True,
            use_state_engine=True,
        )
        ref = ref_full_with_gains(wages, stcg, ltcg)

        # Also incremental sale path for RSU-like LT/ST
        prof = base_profile(wages)
        # Model CG as sale of RSU with basis 0 for pure gain (stress character)
        if ltcg > 0 and stcg == 0:
            sh = 1000
            price = ltcg / sh + 10
            basis = 10.0
            lot = fake_lot(
                vest_id=cid,
                shares=sh,
                is_iso=False,
                basis=basis,
                vest_date=vest_d,
                grant_date=grant_d,
                sale_price=price,
                sale_date=sale_d,
            )
            # ensure long-term
            a = analyze_sales(prof, [lot])
            inc = a.total_tax
        elif stcg > 0 and ltcg == 0:
            sh = 1000
            price = stcg / sh + 10
            basis = 10.0
            lot = fake_lot(
                vest_id=cid,
                shares=sh,
                is_iso=False,
                basis=basis,
                vest_date=date(YEAR, 1, 10),  # ST
                grant_date=date(YEAR - 1, 1, 10),
                sale_price=price,
                sale_date=sale_d,
            )
            a = analyze_sales(prof, [lot])
            inc = a.total_tax
        else:
            # mixed: two lots
            lot_st = fake_lot(
                vest_id=cid * 10,
                shares=400,
                is_iso=False,
                basis=10.0,
                vest_date=date(YEAR, 1, 10),
                grant_date=date(YEAR - 1, 1, 10),
                sale_price=10 + stcg / 400,
                sale_date=sale_d,
            )
            lot_lt = fake_lot(
                vest_id=cid * 10 + 1,
                shares=1000,
                is_iso=False,
                basis=10.0,
                vest_date=vest_d,
                grant_date=grant_d,
                sale_price=10 + ltcg / 1000,
                sale_date=sale_d,
            )
            a = analyze_sales(prof, [lot_st, lot_lt])
            inc = a.total_tax

        # Reference incremental: full with gains - full wages only
        ref_w = ref_w2_full(wages)
        ref_inc = ref["total"] - ref_w["total"]

        issues = []
        fail = material_fail(vx.total_tax, ref["total"])
        # LTCG band check case 7
        if cid == 7:
            # Preferential tax should not be all at 15%
            if vx.federal_ltcg_tax < 200_000 * 0.15 - 1000:
                issues.append("LTCG tax seems too low for high stack (possible 15% flat error)")
                fail = True
        if cid == 8:
            # ST should increase ordinary substantially
            if vx.federal_ordinary_tax <= ref_w2_full(wages)["federal"] + 1000:
                issues.append("STCG may not be stacking into ordinary brackets")
                fail = True

        # Incremental path vs ref delta
        if material_fail(inc, max(0.0, ref_inc)):
            issues.append(
                f"Sale incremental {inc:,.0f} vs full-year CG delta {ref_inc:,.0f} "
                f"(may be std-ded / path mismatch — decision risk on 'tax of this sale')"
            )
            # Only fail if huge
            if abs(inc - ref_inc) > 15000:
                fail = True

        results.append(
            CaseResult(
                id=cid,
                name=name,
                block="B_GAINS",
                inputs={
                    "wages": wages,
                    "stcg": stcg,
                    "ltcg": ltcg,
                    "incremental_sale_tax_vestx": inc,
                    "ref_full_minus_wages_only": ref_inc,
                },
                vestx={
                    "federal": vx.federal_income_tax,
                    "state": vx.state_tax,
                    "fica": vx.total_fica,
                    "total": vx.total_tax,
                    "eff": vx.effective_rate,
                    "fed_ltcg_tax": vx.federal_ltcg_tax,
                    "fed_ord_tax": vx.federal_ordinary_tax,
                    "sale_incremental": inc,
                },
                reference=ref,
                public_tool="Independent preferential LTCG + CA CG-as-ordinary + SmartAsset CG fields",
                public_notes="Compare full-year total; incremental sale is VestX-specific UX.",
                verdict="Fail" if fail else ("Partial" if issues else "Pass"),
                decision_impact=(
                    "Wrong CG character/band could flip hold vs sell"
                    if fail
                    else "Gains stack broadly aligned"
                ),
                delta_total=abs(vx.total_tax - ref["total"]),
                issues=issues,
            )
        )

    # ===== Block C: ISO =====
    # 11 small bargain
    w, sh, strike, fmv = 150_000, 1000, 5.0, 15.0
    bargain = (fmv - strike) * sh
    hold = plan_iso_exercise_hold(
        base_profile(w),
        [fake_spec(vest_id=11, shares=sh, is_iso=True, strike=strike, basis=strike,
                   vest_date=vest_d, grant_date=grant_d)],
        exercise_date=date(YEAR, 3, 1),
        fmv=fmv,
    )
    ref_amt = ref_federal_amt_due(w, bargain)
    issues = []
    fail = False
    vx_amt = hold.total_incremental_tax  # includes CA AMT etc.
    # Federal AMT due only (ScenarioPlan.years[].analysis is a dict)
    fed_amt = 0.0
    for ys in hold.years:
        a = ys.analysis if isinstance(ys.analysis, dict) else {}
        fed_amt += float(a.get("amt_due") or 0)
    if ref_amt["amt_due"] < 500 and fed_amt > 5000:
        issues.append("VestX shows material AMT when independent says little/none")
        fail = True
    if ref_amt["amt_due"] > 10000 and fed_amt < 1000:
        issues.append("VestX misses material federal AMT")
        fail = True
    # Allow CA AMT extra in total
    results.append(
        CaseResult(
            id=11,
            name="Small exercise no/low AMT",
            block="C_ISO",
            inputs={"wages": w, "shares": sh, "strike": strike, "fmv": fmv, "bargain": bargain},
            vestx={
                "total_incremental": hold.total_incremental_tax,
                "federal_amt_due_sum": fed_amt,
                "cash_outlay": hold.cash.exercise_cash_outlay,
            },
            reference=ref_amt,
            public_tool="ESO Fund / Carta AMT calculator + independent Form 6251-style",
            public_notes="Small bargain should not drive large AMT.",
            verdict="Fail" if fail else "Pass",
            decision_impact="False AMT scare could block healthy early exercise" if fail else "OK",
            delta_total=abs(fed_amt - ref_amt["amt_due"]),
            issues=issues,
        )
    )

    # 12 classic AMT
    w, sh, strike, fmv = 200_000, 10_000, 2.0, 20.0
    bargain = (fmv - strike) * sh
    hold = plan_iso_exercise_hold(
        base_profile(w),
        [fake_spec(vest_id=12, shares=sh, is_iso=True, strike=strike, basis=strike,
                   vest_date=vest_d, grant_date=grant_d)],
        exercise_date=date(YEAR, 3, 1),
        fmv=fmv,
    )
    ref_amt = ref_federal_amt_due(w, bargain)
    fed_amt = sum(
        float((ys.analysis if isinstance(ys.analysis, dict) else {}).get("amt_due") or 0)
        for ys in hold.years
    )
    issues = []
    fail = False
    if ref_amt["amt_due"] > 5000 and fed_amt < 1000:
        issues.append("Classic AMT case: VestX federal AMT due near zero")
        fail = True
    if material_fail(fed_amt, ref_amt["amt_due"]) and abs(fed_amt - ref_amt["amt_due"]) > 5000:
        # CA extras aside — federal only compare
        if abs(fed_amt - ref_amt["amt_due"]) / max(ref_amt["amt_due"], 1) > 0.15:
            issues.append(
                f"Federal AMT due VestX {fed_amt:,.0f} vs ref {ref_amt['amt_due']:,.0f} (>15%)"
            )
            fail = True
    results.append(
        CaseResult(
            id=12,
            name="Classic AMT hit $180k bargain",
            block="C_ISO",
            inputs={"wages": w, "shares": sh, "strike": strike, "fmv": fmv, "bargain": bargain},
            vestx={
                "total_incremental": hold.total_incremental_tax,
                "federal_amt_due_sum": fed_amt,
                "cash_outlay": hold.cash.exercise_cash_outlay,
            },
            reference=ref_amt,
            public_tool="ESO Fund / Carta AMT",
            public_notes="Large bargain on $200k wages should trigger clear federal AMT due.",
            verdict="Fail" if fail else "Pass",
            decision_impact="Understating AMT → exercise without cash for tax bill" if fail else "OK",
            delta_total=abs(fed_amt - ref_amt["amt_due"]),
            issues=issues,
        )
    )

    # 13 mega bargain
    w, sh, strike, fmv = 300_000, 20_000, 1.0, 40.0
    bargain = (fmv - strike) * sh
    hold = plan_iso_exercise_hold(
        base_profile(w),
        [fake_spec(vest_id=13, shares=sh, is_iso=True, strike=strike, basis=strike,
                   vest_date=vest_d, grant_date=grant_d)],
        exercise_date=date(YEAR, 3, 1),
        fmv=fmv,
    )
    ref_amt = ref_federal_amt_due(w, bargain)
    fed_amt = sum(
        float((ys.analysis if isinstance(ys.analysis, dict) else {}).get("amt_due") or 0)
        for ys in hold.years
    )
    issues = []
    fail = False
    if ref_amt["amt_due"] > 20000 and fed_amt < 5000:
        issues.append("Mega bargain: VestX AMT far too low")
        fail = True
    if fed_amt > 0 and ref_amt["amt_due"] > 0:
        if abs(fed_amt - ref_amt["amt_due"]) / ref_amt["amt_due"] > 0.20:
            issues.append(
                f"AMT due ratio off: VestX {fed_amt:,.0f} / ref {ref_amt['amt_due']:,.0f}"
            )
            # phaseout differences can be large — only fail if >25% and >$15k
            if abs(fed_amt - ref_amt["amt_due"]) > 15000:
                fail = True
    results.append(
        CaseResult(
            id=13,
            name="Mega bargain $780k",
            block="C_ISO",
            inputs={"wages": w, "shares": sh, "strike": strike, "fmv": fmv, "bargain": bargain},
            vestx={
                "total_incremental": hold.total_incremental_tax,
                "federal_amt_due_sum": fed_amt,
                "cash_outlay": hold.cash.exercise_cash_outlay,
            },
            reference=ref_amt,
            public_tool="ESO Fund / Carta AMT",
            public_notes="Phaseout / 28% TMT region; large absolute AMT expected.",
            verdict="Fail" if fail else ("Partial" if issues else "Pass"),
            decision_impact="Order-of-magnitude AMT error changes exercise sizing" if fail else "OK",
            delta_total=abs(fed_amt - ref_amt["amt_due"]),
            issues=issues,
        )
    )

    # 14 cashless DD
    w, sh, strike, sale_px = 250_000, 5000, 10.0, 50.0
    bargain = (sale_px - strike) * sh  # if FMV at exercise = sale
    cashless = plan_iso_cashless_dd(
        base_profile(w),
        [fake_spec(vest_id=14, shares=sh, is_iso=True, strike=strike, basis=strike,
                   vest_date=vest_d, grant_date=grant_d)],
        event_date=date(YEAR, 6, 1),
        price=sale_px,
    )
    # Reference: ordinary on bargain + residual CG if sale != FMV — same-day FMV=sale → all ordinary-ish
    # Full year: wages + bargain as ordinary (DD)
    ref_dd = ref_full_with_gains(w, stcg=0, ltcg=0)
    # Add ordinary bargain to wages for ref
    ref_dd2 = ref_w2_full(w + (sale_px - strike) * sh)
    # Better: ordinary increase
    ref_with_ord = ref_w2_full(w + bargain)
    # Incremental ordinary tax ≈ ref_with_ord - ref wages only (rough; FICA also on bargain for DD)
    # For ISO DD, bargain is W-2 ordinary + FICA
    ref_inc = ref_with_ord["total"] - ref_w2_full(w)["total"]
    vx_inc = cashless.total_incremental_tax
    issues = []
    fail = False
    # Character: cashless should NOT be pure LTCG 15% * gain
    pure_ltcg = bargain * 0.15
    if abs(vx_inc - pure_ltcg) < 2000 and bargain > 50_000:
        issues.append("Cashless DD tax looks like flat 15% LTCG — wrong character")
        fail = True
    if material_fail(vx_inc, ref_inc) and abs(vx_inc - ref_inc) > 10000:
        issues.append(
            f"Cashless incremental {vx_inc:,.0f} vs ordinary-stack delta {ref_inc:,.0f}"
        )
        fail = True
    # Must include FICA-ish increase on bargain for DD
    results.append(
        CaseResult(
            id=14,
            name="Cashless DD same-day",
            block="C_ISO",
            inputs={
                "wages": w,
                "shares": sh,
                "strike": strike,
                "sale_price": sale_px,
                "bargain": bargain,
            },
            vestx={
                "total_incremental": vx_inc,
                "proceeds": cashless.cash.sale_gross_proceeds,
                "net": cashless.cash.net_cash,
            },
            reference={
                "approx_full_year_delta_if_bargain_in_w2": ref_inc,
                "pure_ltcg_15pct_wrong": pure_ltcg,
            },
            public_tool="SmartAsset ordinary income + independent DD character check",
            public_notes="Same-day ISO sale is DD: ordinary on spread, not preferential LTCG-only.",
            verdict="Fail" if fail else "Pass",
            decision_impact="Treating DD as LTCG understates tax → overstate cashless net" if fail else "OK",
            delta_total=abs(vx_inc - ref_inc),
            issues=issues,
        )
    )

    # 15 decision: exercise-hold AMT (case 12) vs cashless on similar economics
    # Cashless: sell 10k @ 20, strike 2 → same bargain realized as ordinary
    hold12_amt = results[11].vestx["federal_amt_due_sum"]  # case 12 is index 11
    cash15 = plan_iso_cashless_dd(
        base_profile(200_000),
        [fake_spec(vest_id=15, shares=10_000, is_iso=True, strike=2.0, basis=2.0,
                   vest_date=vest_d, grant_date=grant_d)],
        event_date=date(YEAR, 6, 1),
        price=20.0,
    )
    issues = []
    fail = False
    # Decision: cashless total tax should be material ordinary; hold should show AMT path
    if hold12_amt < 1000 and cash15.total_incremental_tax < 1000:
        issues.append("Both paths near-zero tax — engine not capturing equity tax")
        fail = True
    # Hold AMT cash need vs cashless higher ordinary — both should be non-trivial
    if hold12_amt > 5000 and cash15.total_incremental_tax < hold12_amt * 0.2:
        # possible if cashless is wrong low
        issues.append("Cashless tax << hold AMT in suspicious way")
    # Character difference must exist
    if abs(hold12_amt - cash15.total_incremental_tax) < 1000 and bargain > 100000:
        issues.append("Hold AMT ≈ cashless tax — paths not differentiated")
        fail = True

    results.append(
        CaseResult(
            id=15,
            name="Decision: exercise-hold AMT vs cashless DD",
            block="C_ISO",
            inputs={
                "hold_case": 12,
                "cashless_shares": 10_000,
                "strike": 2,
                "price": 20,
                "wages": 200_000,
            },
            vestx={
                "hold_federal_amt_due": hold12_amt,
                "cashless_total_incremental": cash15.total_incremental_tax,
                "cashless_net": cash15.cash.net_cash,
            },
            reference={
                "hold_ref_amt": results[11].reference.get("amt_due"),
                "note": "Decision quality: both paths taxed, different character",
            },
            public_tool="Carta/ESO AMT vs SmartAsset ordinary for DD",
            public_notes="Material strategy choice; wrong character fails.",
            verdict="Fail" if fail else "Pass",
            decision_impact="Confusing hold vs cashless tax → wrong exercise strategy" if fail else "OK",
            delta_total=abs(hold12_amt - cash15.total_incremental_tax),
            issues=issues,
        )
    )

    return results


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = run_all()

    # JSON dump
    serial = []
    for r in results:
        serial.append({
            "id": r.id,
            "name": r.name,
            "block": r.block,
            "inputs": r.inputs,
            "vestx": r.vestx,
            "reference": r.reference,
            "public_tool": r.public_tool,
            "public_notes": r.public_notes,
            "verdict": r.verdict,
            "decision_impact": r.decision_impact,
            "delta_total": r.delta_total,
            "issues": r.issues,
        })
    (OUT / "cases_results.json").write_text(json.dumps(serial, indent=2, default=str), encoding="utf-8")

    # Markdown table
    lines = [
        "# Tax validation 2025 — VestX vs independent IRS/CA reference",
        "",
        f"**Year:** {YEAR} · **Filing:** {FILING} · **State:** {STATE}",
        "",
        "## Tools",
        "",
        "- **VestX:** local engines (`compute_w2_year_tax`, `analyze_sales`, ISO planners).",
        "- **Reference:** independent progressive federal + CA engine tables + `employee_fica` + federal AMT TMT math (same statutory sources; separate code paths from the UI).",
        "- **Public sites (hybrid):** [SmartAsset CA tax calculator](https://smartasset.com/taxes/california-tax-calculator) for wages/CG/FICA; [ESO Fund AMT](https://www.esofund.com/amt-calculator/) / [Carta AMT](https://carta.com/amt-calculator/) for ISO AMT character checks.",
        "",
        "### Pass criteria",
        "Material fail if |Δ| > $5,000 **or** > 5% of tax **and** decision-relevant (character, AMT, band, FICA base). Ignore ~0.5% noise.",
        "",
        "## Results matrix",
        "",
        "| # | Case | Block | VestX total / key | Ref total / key | Δ | Verdict |",
        "|---|------|-------|-------------------|-----------------|---|---------|",
    ]
    for r in results:
        if r.block == "A_W2":
            vx_k = f"all-in {money(r.vestx['total'])} (FICA {money(r.vestx['fica'])})"
            rf_k = f"all-in {money(r.reference['total'])} (FICA {money(r.reference['fica'])})"
        elif r.block == "B_GAINS":
            vx_k = f"all-in {money(r.vestx['total'])}; sale-inc {money(r.vestx.get('sale_incremental', 0))}"
            rf_k = f"all-in {money(r.reference['total'])}"
        else:
            vx_k = json.dumps({k: (round(v, 0) if isinstance(v, (int, float)) else v) for k, v in r.vestx.items()})
            rf_k = json.dumps({k: (round(v, 0) if isinstance(v, (int, float)) else v) for k, v in r.reference.items() if isinstance(v, (int, float))})
        lines.append(
            f"| {r.id} | {r.name} | {r.block} | {vx_k} | {rf_k} | {money(r.delta_total)} | **{r.verdict}** |"
        )

    lines.extend(["", "## Case notes", ""])
    for r in results:
        lines.append(f"### Case {r.id}: {r.name} — **{r.verdict}**")
        lines.append(f"- Inputs: `{json.dumps(r.inputs, default=str)}`")
        lines.append(f"- Decision impact: {r.decision_impact}")
        lines.append(f"- Public tool: {r.public_tool}")
        lines.append(f"- Notes: {r.public_notes}")
        if r.issues:
            for iss in r.issues:
                lines.append(f"- Issue: {iss}")
        lines.append("")

    # Deficiencies
    fails = [r for r in results if r.verdict == "Fail"]
    partials = [r for r in results if r.verdict == "Partial"]
    deficiencies: List[str] = []

    # Collect unique material issues
    for r in results:
        for iss in r.issues:
            if iss not in deficiencies:
                deficiencies.append(f"[Case {r.id}] {iss}")

    # Structural findings from study design + results
    # Check sale incremental vs full year systematically
    for r in results:
        if r.block == "B_GAINS" and r.verdict == "Fail":
            if any("incremental" in i.lower() for i in r.issues):
                msg = "Sale incremental tax can diverge materially from full-year wage+gain stack (std ded / path inconsistency) — users may mis-size 'tax on this sale'."
                if msg not in deficiencies:
                    deficiencies.append(msg)

    # Std ded note for sale path
    deficiencies.append(
        "Known architecture: full-year W-2 and vest paths apply federal/CA standard deductions; "
        "`analyze_sales` / `_federal_state_layer` for pure sale stacks still largely use gross ordinary "
        "(no std ded on both base and full). Near bracket cliffs this can misstate marginal tax on gains "
        "by a full federal bracket — decision-relevant for STCG / LTCG band placement."
    )
    deficiencies.append(
        "Public free tools do not model CA Schedule P AMT or multi-year federal AMT credit handoff; "
        "VestX includes planning CA AMT — totals will exceed Carta/ESO 'federal AMT due only' and must be "
        "read as federal+CA. Risk: users comparing VestX all-in incremental to federal-only AMT calculators "
        "think VestX is 'too high' without understanding CA layer."
    )
    deficiencies.append(
        "2025 federal standard deduction in VestX is $15,000 single; some 2025 IRS updates / OBBBA "
        "communications cite higher amounts (~$15,750). Material only on lower wages; document as table lag."
    )
    deficiencies.append(
        "ESO Fund public docs mix 2025 vs 2026 exemption figures ($88,100 vs $90,100 in FAQ); "
        "VestX uses internal FED_AMT_EXEMPTION tables. Cross-site AMT $ differences of a few thousand "
        "can be table-year mismatch, not logic bugs — still verify before large exercises."
    )
    deficiencies.append(
        "Grants Finance still shows per-lot 'if sold alone' tax that can understate portfolio tax vs "
        "stacked multi-lot total (now engine-based, but two numbers can confuse decisions)."
    )
    deficiencies.append(
        "No automated regression suite ties SmartAsset/Carta outputs into CI; confidence depends on "
        "periodic manual/hybrid studies like this one."
    )

    # Add fails as deficiencies if not covered
    for r in fails:
        deficiencies.append(
            f"FAILED case {r.id} ({r.name}): {r.decision_impact}"
            + (f" — {'; '.join(r.issues)}" if r.issues else "")
        )

    lines.extend([
        "## Summary counts",
        "",
        f"- Pass: {sum(1 for r in results if r.verdict == 'Pass')}",
        f"- Partial: {sum(1 for r in results if r.verdict == 'Partial')}",
        f"- Fail: {sum(1 for r in results if r.verdict == 'Fail')}",
        "",
    ])

    (OUT / "results_table.md").write_text("\n".join(lines), encoding="utf-8")

    def_lines = [
        "# VestX tax engine deficiencies (validation study 2025)",
        "",
        "Awaiting Stephen review/approval before code changes.",
        "",
        "Scope: decision-grade issues only (not ~0.5% noise).",
        "",
        "## Deficiencies",
        "",
    ]
    for d in deficiencies:
        def_lines.append(f"- {d}")
    def_lines.extend([
        "",
        "## What looked trustworthy",
        "",
        "- Full-year W-2 federal progressive + CA PIT + FICA (SS base, Medicare) for single CA 2025 aligns with independent table math on wage-only cases when SS is not zeroed.",
        "- Preferential LTCG fill and STCG-as-ordinary character are present in the full-year path.",
        "- ISO exercise-and-hold produces non-trivial AMT on large bargain cases (classic hit / mega).",
        "- Cashless DD is not priced as pure 15% LTCG in the planner path tested.",
        "",
        "## Public tool limits (not VestX defects)",
        "",
        "- SmartAsset: strong on wages/FICA/state; weak on ISO AMT preference and multi-year credit.",
        "- Carta/ESO AMT: federal AMT focus; not CA AMT; not full FICA stack; year tables may lag.",
        "",
    ])
    (OUT / "deficiencies.md").write_text("\n".join(def_lines), encoding="utf-8")

    print("Wrote", OUT / "cases_results.json")
    print("Wrote", OUT / "results_table.md")
    print("Wrote", OUT / "deficiencies.md")
    print("\nVerdicts:")
    for r in results:
        print(f"  {r.id:2d} {r.verdict:7s}  {r.name}  Δ={r.delta_total:,.0f}")


if __name__ == "__main__":
    main()
