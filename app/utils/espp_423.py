"""
IRC §423 employee stock purchase plans — qualifying vs disqualifying.

This is the SSOT for VestX. Every sale planner, optimizer, ledger estimate,
and year-income stack must go through ``analyze_espp_sale``. Never treat
statutory ESPP as an RSU (FMV-at-purchase basis, ordinary $0).

Citations: IRC §423; IRS Pub 525 (ESPP); Form 3922.

Qualifying disposition (QD)
    Hold ≥ 2 years from the **beginning of the offering period** AND
    ≥ 1 year from the purchase date.
    Ordinary compensation = lesser of:
      (1) grant/offering-date FMV − purchase price  (the §423 discount at grant)
      (2) actual gain (sale proceeds − purchase price)
    Remainder is capital gain. LTCG if held ≥ 1 year from purchase.

Disqualifying disposition (DD)
    Anything that is not QD.
    If sale price ≥ purchase price:
      Ordinary = lesser of (purchase-date FMV − purchase price) and actual gain.
      Residual capital gain = proceeds − (purchase price + ordinary).
      Typically: ordinary = bargain at purchase; CG = sale − purchase FMV.
    If sale price < purchase price:
      Ordinary $0; capital loss vs purchase price.

Lookback purchase price
    (1 − discount) × min(FMV at offering/grant, FMV at purchase). Statutory
    §423 discount cap is 15%.

$25,000 limit
    FMV at grant × shares granted under the offering, per calendar year.
    Excess may be non-statutory (NQESPP) — flagged, not silently ignored.

FICA
    Statutory §423 compensation (QD or DD ordinary) is **not** FICA.
    NQESPP ordinary is wages for FICA.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Dict, List, Optional

from dateutil.relativedelta import relativedelta

from app.utils.tax_constants import ESPP_ANNUAL_LIMIT

STATUTORY_DISCOUNT_CAP = 0.15
DEFAULT_OFFERING_MONTHS = 6


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, month=2, day=28)


def offering_start_for(
    *,
    stored: Optional[date] = None,
    grant_date: Optional[date] = None,
    purchase_date: Optional[date] = None,
    offering_months: int = DEFAULT_OFFERING_MONTHS,
) -> date:
    """
    Offering-period start for the §423 2-year clock.

    Prefer an explicit stored offering start. If grant_date is materially
    before purchase, grant_date *is* the offering start. VestX historically
    stored ESPP grant_date as the purchase/receipt date — in that case assume
    a standard 6-month offering ending on purchase.
    """
    if stored:
        return stored
    if grant_date and purchase_date and (purchase_date - grant_date).days > 30:
        return grant_date
    base = purchase_date or grant_date
    if base is None:
        raise ValueError('ESPP offering start needs a grant or purchase date')
    months = max(1, int(offering_months or DEFAULT_OFFERING_MONTHS))
    return base - relativedelta(months=months)


def lookback_purchase_price(
    grant_fmv: float,
    purchase_fmv: float,
    discount: float,
) -> float:
    disc = min(STATUTORY_DISCOUNT_CAP, max(0.0, float(discount or 0)))
    cands = [float(x) for x in (grant_fmv, purchase_fmv) if x and float(x) > 0]
    lookback = min(cands) if cands else 0.0
    if lookback <= 0:
        return 0.0
    return lookback * (1.0 - disc)


def classify_espp_disposition(
    offering_start: date,
    purchase_date: date,
    sale_date: date,
) -> tuple:
    """Return (qualifying|disqualifying, first_qd_date)."""
    qd_on = max(add_years(offering_start, 2), add_years(purchase_date, 1))
    if sale_date >= qd_on:
        return 'qualifying', qd_on
    return 'disqualifying', qd_on


def disposition_code(kind: str) -> str:
    if kind == 'qualifying':
        return 'QD'
    if kind == 'disqualifying':
        return 'DD'
    return kind or 'n/a'


@dataclass
class EsppSaleResult:
    disposition: str  # qualifying | disqualifying
    disposition_code: str  # QD | DD
    ordinary_income: float
    capital_gain: float
    is_long_term: bool
    purchase_price_per_share: float
    purchase_basis: float
    offering_start: date
    purchase_date: date
    first_qd_date: date
    holding_days: int
    statutory: bool
    fica_ordinary: float
    annual_limit_excess: float
    notes: List[str] = field(default_factory=list)

    @property
    def ordinary_bargain(self) -> float:
        return self.ordinary_income

    @property
    def cg_remainder(self) -> float:
        return self.capital_gain

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['offering_start'] = self.offering_start.isoformat()
        d['purchase_date'] = self.purchase_date.isoformat()
        d['first_qd_date'] = self.first_qd_date.isoformat()
        d['ordinary_bargain'] = self.ordinary_income
        d['cg_remainder'] = self.capital_gain
        return d


def analyze_espp_sale(
    *,
    shares: float,
    sale_price: float,
    sale_date: date,
    purchase_date: date,
    grant_date: Optional[date] = None,
    offering_start: Optional[date] = None,
    grant_fmv: float = 0.0,
    purchase_fmv: float = 0.0,
    discount: float = 0.15,
    commission: float = 0.0,
    statutory: bool = True,
) -> EsppSaleResult:
    notes: List[str] = []
    sh = max(0.0, float(shares or 0))
    proceeds = sh * float(sale_price or 0) - float(commission or 0)
    disc = float(discount or 0) or 0.15
    if statutory and disc > STATUTORY_DISCOUNT_CAP + 1e-9:
        notes.append(
            f'Discount {disc*100:.1f}% exceeds §423 15% cap — excess may be NQESPP.'
        )
        disc = STATUTORY_DISCOUNT_CAP

    fmv_g = float(grant_fmv or 0)
    fmv_p = float(purchase_fmv or 0)
    if fmv_p <= 0:
        fmv_p = float(sale_price or 0)

    offer = offering_start_for(
        stored=offering_start,
        grant_date=grant_date,
        purchase_date=purchase_date,
    )
    purchase_px = lookback_purchase_price(fmv_g, fmv_p, disc)
    if purchase_px <= 0:
        # Last resort: 85% of whatever FMV we have
        purchase_px = lookback_purchase_price(fmv_g or fmv_p, fmv_p or fmv_g, disc)

    kind, qd_on = classify_espp_disposition(offer, purchase_date, sale_date)
    purchase_basis = purchase_px * sh
    actual_gain = proceeds - purchase_basis
    holding_days = (sale_date - purchase_date).days
    is_lt = holding_days >= 365

    annual_excess = 0.0
    if fmv_g > 0 and sh * fmv_g > ESPP_ANNUAL_LIMIT:
        annual_excess = sh * fmv_g - ESPP_ANNUAL_LIMIT
        notes.append(
            f'Offering FMV ${sh * fmv_g:,.0f} exceeds ${ESPP_ANNUAL_LIMIT:,.0f} '
            f'§423 annual limit — excess may be non-statutory.'
        )

    if not statutory:
        # NQESPP: bargain at purchase is wages (FICA); sale vs purchase FMV is CG.
        ordinary = max(0.0, (fmv_p - purchase_px) * sh)
        capital_gain = proceeds - (purchase_basis + ordinary)
        notes.append(
            'Non-qualified ESPP: bargain at purchase is ordinary wages (FICA); '
            'sale vs purchase-date FMV is capital gain.'
        )
        fica_ord = ordinary
        kind = 'disqualifying'
    elif kind == 'qualifying':
        grant_bargain = max(0.0, (fmv_g or purchase_px / max(1e-9, 1.0 - disc)) - purchase_px) * sh
        if fmv_g > 0:
            grant_bargain = max(0.0, fmv_g - purchase_px) * sh
        if actual_gain <= 0:
            ordinary = 0.0
            capital_gain = actual_gain
            notes.append(
                'ESPP qualifying disposition: sold at/below purchase price — '
                'no ordinary; capital loss vs purchase price.'
            )
        else:
            ordinary = min(grant_bargain, actual_gain)
            capital_gain = actual_gain - ordinary
            notes.append(
                f'ESPP qualifying disposition (§423): ordinary = lesser of grant-date '
                f'discount (${grant_bargain:,.2f}) and actual gain; rest capital gain. '
                f'Purchase ${purchase_px:.2f}/sh. Offering {offer.isoformat()}.'
            )
        fica_ord = 0.0
    else:
        bargain = max(0.0, fmv_p - purchase_px) * sh
        if actual_gain <= 0:
            ordinary = 0.0
            capital_gain = actual_gain
            notes.append(
                'ESPP disqualifying: sold at/below purchase price — ordinary $0; '
                f'capital loss vs purchase ${purchase_px:.2f}/sh. '
                f'QD window opens {qd_on.isoformat()}.'
            )
        else:
            ordinary = min(bargain, actual_gain)
            capital_gain = actual_gain - ordinary
            notes.append(
                f'ESPP disqualifying disposition: bargain at purchase '
                f'(FMV ${fmv_p:.2f} − purchase ${purchase_px:.2f}) as ordinary, '
                f'capped at actual gain; residual capital gain. '
                f'QD opens {qd_on.isoformat()} (2y from offering {offer.isoformat()} '
                f'and 1y from purchase).'
            )
        fica_ord = 0.0

    return EsppSaleResult(
        disposition=kind,
        disposition_code=disposition_code(kind),
        ordinary_income=round(ordinary, 2),
        capital_gain=round(capital_gain, 2),
        is_long_term=is_lt,
        purchase_price_per_share=round(purchase_px, 4),
        purchase_basis=round(purchase_basis, 2),
        offering_start=offer,
        purchase_date=purchase_date,
        first_qd_date=qd_on,
        holding_days=holding_days,
        statutory=bool(statutory),
        fica_ordinary=round(fica_ord, 2),
        annual_limit_excess=round(annual_excess, 2),
        notes=notes,
    )
