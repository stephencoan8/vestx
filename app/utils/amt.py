"""
Federal and California AMT + minimum-tax credit tracking.

Planning model (not a substitute for Form 6251 / CA Schedule P):
- Federal TMT at 26%/28% on AMTI after exemption/phaseout
- ISO bargain element is a *deferral* preference → generates federal AMT credit
- CA Schedule P–style AMT at flat 7% after CA exemption/phaseout
- Credits: usable only when regular tax exceeds TMT (each jurisdiction)
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple


def _year_pick(table: dict, year: int):
    if year in table:
        return table[year]
    years = sorted(table.keys())
    if year < years[0]:
        return table[years[0]]
    return table[years[-1]]


# --- Federal AMT ---
# 2025: TCJA-style high phaseout start, 25% phaseout rate
# 2026: lower phaseout starts, 50% phaseout (post-OBBBA planning tables)
FED_AMT_EXEMPTION = {
    2025: {'single': 88100, 'hoh': 88100, 'mfj': 137000, 'mfs': 68500},
    2026: {'single': 90100, 'hoh': 90100, 'mfj': 140200, 'mfs': 70100},
}
FED_AMT_PHASEOUT_START = {
    2025: {'single': 626350, 'hoh': 626350, 'mfj': 1_252_700, 'mfs': 626350},
    2026: {'single': 500_000, 'hoh': 500_000, 'mfj': 1_000_000, 'mfs': 500_000},
}
FED_AMT_PHASEOUT_RATE = {2025: 0.25, 2026: 0.50}
FED_AMT_RATE_LOW = 0.26
FED_AMT_RATE_HIGH = 0.28
# 28% threshold on taxable AMTI (after exemption) — approx 2025/26
FED_AMT_28_THRESHOLD = {
    'single': 220700,
    'hoh': 220700,
    'mfj': 220700,
    'mfs': 110350,
}


# --- California AMT (Schedule P 540 style) ---
# 2025 FTB figures; 2026 ~3% inflation planning estimate
CA_AMT_RATE = 0.07
CA_AMT_EXEMPTION = {
    2025: {'single': 92749, 'hoh': 92749, 'mfj': 123667, 'mfs': 61830},
    2026: {'single': 95500, 'hoh': 95500, 'mfj': 127400, 'mfs': 63700},
}
# Phaseout begins when AMTI exceeds these (exemption reduced 25% of excess)
CA_AMT_PHASEOUT_START = {
    2025: {'single': 347808, 'hoh': 347808, 'mfj': 463745, 'mfs': 231868},
    2026: {'single': 358200, 'hoh': 358200, 'mfj': 477700, 'mfs': 238800},
}
CA_AMT_PHASEOUT_RATE = 0.25


@dataclass
class AmtLayerResult:
    """One jurisdiction's AMT vs regular comparison + credit rollforward."""
    jurisdiction: str  # 'federal' | 'CA'
    amti: float
    exemption_used: float
    tentative_minimum_tax: float
    regular_tax: float  # tax compared to TMT (excludes NIIT federally)
    amt_due: float  # max(0, TMT - regular)
    credit_opening: float
    credit_generated: float  # from this year's AMT (deferral prefs)
    credit_used: float
    credit_ending: float
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CombinedAmtResult:
    federal: AmtLayerResult
    california: Optional[AmtLayerResult]
    # Tax dollars to add: federal AMT due − credit used (credit reduces regular)
    federal_amt_net: float  # amt_due - 0; credit_used applied to regular elsewhere
    ca_amt_due: float
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'federal': self.federal.to_dict(),
            'california': self.california.to_dict() if self.california else None,
            'federal_amt_net': self.federal_amt_net,
            'ca_amt_due': self.ca_amt_due,
            'notes': self.notes,
        }


def compute_federal_tmt(
    amti: float,
    filing: str,
    year: int,
    *,
    ltcg: float = 0.0,
) -> Tuple[float, float]:
    """
    Return (TMT, exemption_used).

    Planning Form 6251-style: after exemption, the long-term capital gain
    slice of taxable AMTI is taxed at preferential 0/15/20% rates (not 26/28%).
    ISO bargain and other ordinary AMTI remain at 26/28%. Without this split,
    pure large LTCG stacks falsely create ~$30k+ phantom AMT vs regular tax.
    """
    if amti <= 0:
        return 0.0, 0.0
    filing = filing if filing in ('single', 'mfj', 'mfs', 'hoh') else 'single'
    exempt = float(_year_pick(FED_AMT_EXEMPTION, year).get(filing, 90100))
    phase_start = float(_year_pick(FED_AMT_PHASEOUT_START, year).get(filing, 500_000))
    if year in FED_AMT_PHASEOUT_RATE:
        phase_rate = FED_AMT_PHASEOUT_RATE[year]
    else:
        nearest = min(FED_AMT_PHASEOUT_RATE.keys(), key=lambda y: abs(y - year))
        phase_rate = FED_AMT_PHASEOUT_RATE[nearest]

    if amti > phase_start:
        exempt = max(0.0, exempt - phase_rate * (amti - phase_start))
    taxable = max(0.0, amti - exempt)
    if taxable <= 0:
        return 0.0, exempt

    # Preferential LTCG under AMT (top of taxable AMTI; exemption hits ordinary first)
    ltcg_pref = max(0.0, min(float(ltcg or 0.0), taxable))
    ordinary_taxable_amt = max(0.0, taxable - ltcg_pref)

    thr = FED_AMT_28_THRESHOLD.get(filing, 220700)
    if ordinary_taxable_amt <= thr:
        tmt_ord = ordinary_taxable_amt * FED_AMT_RATE_LOW
    else:
        tmt_ord = (
            thr * FED_AMT_RATE_LOW
            + (ordinary_taxable_amt - thr) * FED_AMT_RATE_HIGH
        )

    tmt_cg = 0.0
    if ltcg_pref > 0:
        # Lazy import avoids circular import with tax_engine at module load
        from app.utils.tax_engine import preferential_ltcg_tax
        tmt_cg, _ = preferential_ltcg_tax(ltcg_pref, ordinary_taxable_amt, filing, year)

    return tmt_ord + tmt_cg, exempt


def compute_ca_tmt(amti: float, filing: str, year: int) -> Tuple[float, float]:
    """Return (CA TMT at 7%, exemption_used)."""
    if amti <= 0:
        return 0.0, 0.0
    filing = filing if filing in ('single', 'mfj', 'mfs', 'hoh') else 'single'
    exempt = float(_year_pick(CA_AMT_EXEMPTION, year).get(filing, 92749))
    phase_start = float(_year_pick(CA_AMT_PHASEOUT_START, year).get(filing, 347808))
    if amti > phase_start:
        exempt = max(0.0, exempt - CA_AMT_PHASEOUT_RATE * (amti - phase_start))
    taxable = max(0.0, amti - exempt)
    return taxable * CA_AMT_RATE, exempt


def apply_amt_and_credit(
    *,
    jurisdiction: str,
    amti: float,
    regular_tax: float,
    tmt: float,
    exemption_used: float,
    opening_credit: float,
    generate_credit_from_amt: bool = True,
) -> AmtLayerResult:
    """
    Compare regular tax to TMT; apply prior-year minimum tax credit.

    Credit usable only to the extent regular_tax > TMT.
    New credit generated ≈ this year's amt_due when from deferral prefs (ISO).
    """
    notes: List[str] = []
    opening = max(0.0, float(opening_credit or 0.0))
    regular = max(0.0, float(regular_tax or 0.0))
    tmt = max(0.0, float(tmt or 0.0))

    if tmt > regular:
        amt_due = tmt - regular
        credit_used = 0.0
        credit_generated = amt_due if generate_credit_from_amt else 0.0
        ending = opening + credit_generated
        notes.append(
            f'{jurisdiction}: AMT due ${amt_due:,.0f} (TMT ${tmt:,.0f} − regular ${regular:,.0f}). '
            f'Credit carryforward becomes ${ending:,.0f}.'
        )
    else:
        amt_due = 0.0
        room = regular - tmt
        credit_used = min(opening, room)
        credit_generated = 0.0
        ending = opening - credit_used
        if credit_used > 0:
            notes.append(
                f'{jurisdiction}: Used ${credit_used:,.0f} of AMT credit '
                f'(room regular−TMT = ${room:,.0f}). Ending credit ${ending:,.0f}.'
            )
        elif opening > 0:
            notes.append(
                f'{jurisdiction}: TMT ${tmt:,.0f} ≥ regular room — no credit used. '
                f'Opening credit ${opening:,.0f} remains.'
            )

    return AmtLayerResult(
        jurisdiction=jurisdiction,
        amti=amti,
        exemption_used=exemption_used,
        tentative_minimum_tax=tmt,
        regular_tax=regular,
        amt_due=amt_due,
        credit_opening=opening,
        credit_generated=credit_generated,
        credit_used=credit_used,
        credit_ending=ending,
        notes=notes,
    )


def compute_amt_stack(
    *,
    filing: str,
    year: int,
    # Federal regular tax for AMT comparison (ordinary + LTCG progressive, NOT NIIT)
    federal_regular_tax: float,
    # CA regular PIT (before MHST is sometimes separate; we compare to CA TMT on same AMTI)
    ca_regular_tax: float,
    # AMTI components
    ordinary_and_cg_base: float,
    iso_bargain_preference: float,
    federal_credit_opening: float = 0.0,
    ca_credit_opening: float = 0.0,
    state_code: Optional[str] = None,
    compute_ca: bool = True,
    # LTCG included in ordinary_and_cg_base — taxed at pref rates under federal TMT
    long_term_gains: float = 0.0,
) -> CombinedAmtResult:
    """
    Full federal (+ optional CA) AMT with credit rollforward.

    AMTI ≈ ordinary + ST/LT gains + ISO bargain preference (simplified).
    Federal TMT applies preferential rates to the LTCG slice (Form 6251-style);
    ISO bargain stays in the 26/28% ordinary AMT slice.
    """
    amti = max(0.0, ordinary_and_cg_base) + max(0.0, iso_bargain_preference)
    ltcg = max(0.0, float(long_term_gains or 0.0))
    fed_tmt, fed_ex = compute_federal_tmt(amti, filing, year, ltcg=ltcg)
    federal = apply_amt_and_credit(
        jurisdiction='federal',
        amti=amti,
        regular_tax=federal_regular_tax,
        tmt=fed_tmt,
        exemption_used=fed_ex,
        opening_credit=federal_credit_opening,
        # Credit generation only when ISO bargain (or other AMT preference) actually exists
        generate_credit_from_amt=bool(iso_bargain_preference and iso_bargain_preference > 0),
    )

    california = None
    ca_due = 0.0
    notes: List[str] = list(federal.notes)
    code = (state_code or '').upper()
    if compute_ca and code == 'CA':
        ca_tmt, ca_ex = compute_ca_tmt(amti, filing, year)
        california = apply_amt_and_credit(
            jurisdiction='CA',
            amti=amti,
            regular_tax=ca_regular_tax,
            tmt=ca_tmt,
            exemption_used=ca_ex,
            opening_credit=ca_credit_opening,
            generate_credit_from_amt=True,
        )
        ca_due = california.amt_due
        notes.extend(california.notes)
        notes.append(
            'CA AMT is Schedule P–style at 7% on AMTI after CA exemption (planning estimate).'
        )

    return CombinedAmtResult(
        federal=federal,
        california=california,
        federal_amt_net=federal.amt_due,
        ca_amt_due=ca_due,
        notes=notes,
    )


def project_credit_years(
    year_results: List[Dict[str, Any]],
    *,
    opening_federal: float,
    opening_ca: float,
) -> List[Dict[str, Any]]:
    """
    Given ordered year slices that each contain federal/ca AmtLayerResult dicts,
    re-chain credit_ending → next opening (already done if computed sequentially).
    Returns a display ledger.
    """
    ledger = []
    fed_bal = opening_federal
    ca_bal = opening_ca
    for yr in year_results:
        fed = yr.get('federal') or {}
        ca = yr.get('california') or {}
        ledger.append({
            'tax_year': yr.get('tax_year'),
            'federal_opening': fed.get('credit_opening', fed_bal),
            'federal_generated': fed.get('credit_generated', 0),
            'federal_used': fed.get('credit_used', 0),
            'federal_ending': fed.get('credit_ending', fed_bal),
            'ca_opening': ca.get('credit_opening', ca_bal) if ca else ca_bal,
            'ca_generated': ca.get('credit_generated', 0) if ca else 0,
            'ca_used': ca.get('credit_used', 0) if ca else 0,
            'ca_ending': ca.get('credit_ending', ca_bal) if ca else ca_bal,
            'federal_amt_due': fed.get('amt_due', 0),
            'ca_amt_due': ca.get('amt_due', 0) if ca else 0,
        })
        fed_bal = ledger[-1]['federal_ending']
        ca_bal = ledger[-1]['ca_ending']
    return ledger
