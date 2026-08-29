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
    assert r.sdi == pytest.approx(400_000 * CA_SDI_RATE)
    # SS maxed; Medicare 1.45 + Add'l 0.9 = 2.35; SDI 1.3
    assert abs(r.fica_marginal - 0.0235) < 1e-6
    assert abs(r.sdi_marginal - CA_SDI_RATE) < 1e-9
    assert abs(
        r.combined_ordinary_marginal
        - (r.ordinary_marginal + r.state_marginal + 0.0235 + CA_SDI_RATE)
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
    assert ca_sdi(100_000) == pytest.approx(100_000 * CA_SDI_RATE)


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


def test_safe_harbor_line_no_penalty_april_bill():
    from app.utils.cash_vs_tax import _safe_harbor_line
    line = _safe_harbor_line(
        tax_year=2026,
        prior_tax=35_455,
        prior_source='entered',
        harbor={
            'prior_year_safe_harbor': 39_000,
            'current_year_90pct': 50_000,
            'required_annual': 39_000,
            'high_agi_110': True,
        },
        ytd_credits=141_674,
        no_penalty=True,
        april_balance=55_766,
    )
    assert '110% of 2025 tax' in line or '110% of 2024 tax' in line or '110%' in line
    assert 'No penalty' in line
    assert 'April bill' in line
    assert '39,000' in line


def test_grant_type_labels_not_enums():
    from app.utils.share_labels import grant_type_label, lot_kind_line
    assert grant_type_label('kickass') == 'Special'
    assert grant_type_label('new_hire') == 'New hire'
    assert grant_type_label('annual_performance') == 'Annual performance'
    assert lot_kind_line('espp', 'rsu') == 'ESPP'
    assert lot_kind_line('new_hire', 'rsu') == 'New hire RSU'
    assert 'ESPP ESPP' not in lot_kind_line('espp', 'espp')


def test_set_aside_recon_adds_ledger_sales_to_true_up():
    from app.utils.cash_vs_tax import set_aside_recon
    r = set_aside_recon(13_228, 55_766)
    assert r['sales'] == 13228
    assert r['vest_true_up'] == 42538
    assert r['total'] == 55766
    assert r['sales'] + r['vest_true_up'] == r['total']


def test_remaining_quarters_sum_to_april_due():
    from app.utils.cash_vs_tax import allocate_remaining_quarters
    qs = allocate_remaining_quarters(
        april_balance=55_766,
        federal_tax=160_000,
        state_tax=60_000,
        expected_tax=220_000,
        as_of=date(2026, 8, 29),
        tax_year=2026,
    )
    remaining = [q for q in qs if not q['is_past']]
    assert {q['label'] for q in remaining} == {'Q3', 'Q4'}
    assert sum(q['suggested_payment'] for q in remaining) == pytest.approx(55_766, abs=0.02)
    assert qs[0]['suggested_payment'] == 0
    assert qs[1]['suggested_payment'] == 0
    assert qs[2]['ca_payment'] == 0  # Q3 is 0% CA
    assert qs[2]['suggested_payment'] > 0
    assert qs[3]['ca_payment'] > 0
    assert qs[2]['suggested_payment'] + qs[3]['suggested_payment'] == pytest.approx(55_766, abs=0.02)


def test_vpdi_rate_is_1_1_not_statutory_sdi():
    from app.utils.tax_constants import CA_SDI_RATE, CA_SDI_RATE_STATUTORY, CA_SDI_LABEL
    assert CA_SDI_RATE == pytest.approx(0.011)
    assert CA_SDI_RATE_STATUTORY == pytest.approx(0.013)
    assert CA_SDI_LABEL == 'VPDI'


def test_espp_not_box1_kind():
    from app.utils.wage_year_tax import vest_w2_kind
    assert vest_w2_kind('espp', 'rsu') == 'espp'
    assert vest_w2_kind('espp', 'espp') == 'espp'
    assert vest_w2_kind('nqespp', 'rsu') == 'espp'
    assert vest_w2_kind('new_hire', 'rsu') == 'rsu'
    assert vest_w2_kind('new_hire', 'iso_5y') == 'iso'


def test_prior_year_tax_is_entered_only():
    from app.utils.cash_vs_tax import _prior_year_income_tax
    t, src = _prior_year_income_tax(None, 2026, None, 50_000)
    assert t == 50_000 and src == 'entered'
    t, src = _prior_year_income_tax(None, 2026, None, None)
    assert t == 0.0 and src == 'missing'


def test_safe_harbor_line_says_100_when_not_high_agi():
    from app.utils.cash_vs_tax import _safe_harbor_line
    line = _safe_harbor_line(
        tax_year=2026,
        prior_tax=32_570,
        prior_source='computed',
        harbor={
            'prior_year_safe_harbor': 32_570,
            'current_year_90pct': 50_000,
            'required_annual': 32_570,
            'high_agi_110': False,
        },
        ytd_credits=141_674,
        no_penalty=True,
        april_balance=55_766,
    )
    assert '100% of 2025 tax' in line
    assert '110%' not in line


def test_withholding_stub_prompt_when_blank():
    from app.utils.cash_vs_tax import withholding_stub_prompt
    p = withholding_stub_prompt(False, False)
    assert p and 'not a guess' in p
    assert withholding_stub_prompt(True, True) is None


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
