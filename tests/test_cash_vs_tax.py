"""Withholding vs liability, supplemental rates, CA 540-ES, CA std, SDI, ESPP."""

from datetime import date

import pytest

from app.utils.tax_constants import CA_STD_DEDUCTION, amt_28_threshold, CA_ES_FRACTIONS, CA_SDI_RATE
from app.utils.withholding import (
    entered_amount,
    federal_supplemental_withholding,
    ca_supplemental_withholding,
    ca_sdi,
)
from app.utils.wage_year_tax import compute_w2_year_tax
from app.utils.tax_engine import LotSaleInput, analyze_lot
from app.utils.estimated_tax_calendar import federal_estimated_due_dates


def test_entered_zero_is_not_entered():
    assert entered_amount(0) is None
    assert entered_amount(0.0) is None
    assert entered_amount('') is None
    assert entered_amount(None) is None
    assert entered_amount(12000) == 12000


def test_ca_std_2026_is_5706_not_inflated():
    assert CA_STD_DEDUCTION[2026]['single'] == 5706
    r = compute_w2_year_tax(tax_year=2026, filing_status='single', state_code='CA', wages=80_000)
    assert r.ca_std_deduction == 5706


def test_sdi_on_ca_wages_and_marginal():
    r = compute_w2_year_tax(
        tax_year=2026, filing_status='single', state_code='CA', wages=400_000, include_fica=True
    )
    assert r.sdi == pytest.approx(400_000 * 0.013)
    # SS maxed; Medicare 1.45 + Add'l 0.9 = 2.35; SDI 1.3
    assert abs(r.fica_marginal - 0.0235) < 1e-6
    assert abs(r.sdi_marginal - CA_SDI_RATE) < 1e-9
    assert abs(
        r.combined_ordinary_marginal
        - (r.ordinary_marginal + r.state_marginal + 0.0235 + 0.013)
    ) < 1e-6


def test_fed_supplemental_22_then_37():
    assert federal_supplemental_withholding(100_000, ytd_supplemental_before=0) == pytest.approx(22_000)
    # Already $1M supplemental YTD → 37%
    assert federal_supplemental_withholding(50_000, ytd_supplemental_before=1_000_000) == pytest.approx(18_500)
    # Crossing $1M: 200k room at 22% + 50k at 37%
    w = federal_supplemental_withholding(250_000, ytd_supplemental_before=800_000)
    assert w == pytest.approx(200_000 * 0.22 + 50_000 * 0.37)


def test_ca_supplemental_and_sdi():
    assert ca_supplemental_withholding(100_000) == pytest.approx(10_230)
    assert ca_sdi(100_000) == pytest.approx(1_300)


def test_ca_es_fractions():
    assert CA_ES_FRACTIONS == [0.30, 0.40, 0.00, 0.30]
    qs = federal_estimated_due_dates(2026)
    assert qs[2]['due'] == date(2026, 9, 15)
    assert qs[3]['due'] == date(2027, 1, 15)


def test_amt_28_breakpoint_2026():
    assert amt_28_threshold(2026, 'single') == 244500


def test_espp_qualifying_discount_ordinary():
    lot = LotSaleInput(
        vest_event_id=1,
        grant_id=1,
        share_type='rsu',
        grant_type='espp',
        shares=100,
        sale_price=80.0,
        sale_date=date(2026, 6, 1),
        vest_date=date(2024, 4, 15),
        grant_date=date(2024, 4, 15),
        cost_basis_per_share=40.0,
        espp_discount=0.15,
        fmv_at_grant=50.0,
        fmv_at_purchase=50.0,
    )
    # 2y from grant + 1y from purchase both 2026-04-15; sale Jun 2026 is QD
    r = analyze_lot(lot)
    assert r.iso_disposition == 'qualifying'
    assert r.ordinary_income > 0
    # Purchase 85% of 50 = 42.50; grant bargain 50-42.50 = 7.50/sh
    assert r.ordinary_income == pytest.approx(750.0)


def test_espp_disqualifying_if_sold_early():
    lot = LotSaleInput(
        vest_event_id=1,
        grant_id=1,
        share_type='rsu',
        grant_type='espp',
        shares=100,
        sale_price=80.0,
        sale_date=date(2026, 4, 20),
        vest_date=date(2026, 4, 15),
        grant_date=date(2026, 4, 15),
        cost_basis_per_share=40.0,
        espp_discount=0.15,
        fmv_at_grant=50.0,
        fmv_at_purchase=50.0,
    )
    r = analyze_lot(lot)
    assert r.iso_disposition == 'disqualifying'
    # Bargain at purchase 50-42.50 = 7.50
    assert r.ordinary_income == pytest.approx(750.0)
