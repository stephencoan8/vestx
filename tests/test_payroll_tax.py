"""
Validation suite: employee FICA against IRS Pub 15 style expectations.

These are the textbook cases — if they fail, vest/W-2/sale FICA is wrong.
"""

from __future__ import annotations

import pytest

from app.utils.payroll_tax import (
    SS_EMPLOYEE_RATE,
    MEDICARE_EMPLOYEE_RATE,
    ADDITIONAL_MEDICARE_RATE,
    employee_fica,
    employee_fica_full_year,
    ss_wage_base_for_year,
)
from app.utils.tax_engine import compute_vest_ordinary_tax, compute_fica_components
from app.utils.wage_year_tax import compute_w2_year_tax


class TestEmployeeFicaBasics:
    def test_ss_wage_bases(self):
        assert ss_wage_base_for_year(2024) == 168_600
        assert ss_wage_base_for_year(2025) == 176_100
        assert ss_wage_base_for_year(2026) == 184_500

    def test_full_year_under_base_2024(self):
        """$100k wages: full 6.2% SS + 1.45% Medicare, no Add'l Medicare."""
        r = employee_fica_full_year(annual_wages=100_000, tax_year=2024, filing_status='single')
        assert r.social_security == pytest.approx(100_000 * 0.062)
        assert r.medicare == pytest.approx(100_000 * 0.0145)
        assert r.additional_medicare == pytest.approx(0.0)
        assert r.total == pytest.approx(100_000 * 0.0765)

    def test_full_year_over_ss_base_2024(self):
        """$200k single: SS capped at wage base; Medicare on all; no Add'l Med (thr 200k)."""
        r = employee_fica_full_year(annual_wages=200_000, tax_year=2024, filing_status='single')
        assert r.ss_taxable_wages == pytest.approx(168_600)
        assert r.social_security == pytest.approx(168_600 * 0.062)
        assert r.medicare == pytest.approx(200_000 * 0.0145)
        # Threshold is 200k — wages at exactly 200k → $0 additional
        assert r.additional_medicare == pytest.approx(0.0)

    def test_full_year_additional_medicare_single(self):
        """$250k single: Add'l Medicare on $50k."""
        r = employee_fica_full_year(annual_wages=250_000, tax_year=2024, filing_status='single')
        assert r.additional_medicare == pytest.approx(50_000 * 0.009)
        assert r.social_security == pytest.approx(168_600 * 0.062)
        assert r.medicare == pytest.approx(250_000 * 0.0145)

    def test_full_year_mfj_threshold(self):
        """MFJ Add'l Medicare threshold is $250k."""
        r = employee_fica_full_year(annual_wages=250_000, tax_year=2024, filing_status='mfj')
        assert r.additional_medicare == pytest.approx(0.0)
        r2 = employee_fica_full_year(annual_wages=300_000, tax_year=2024, filing_status='mfj')
        assert r2.additional_medicare == pytest.approx(50_000 * 0.009)


class TestSsPhaseOutRemainingBase:
    """SS does not apply a reduced % on the whole check — only remaining base is taxed at 6.2%."""

    def test_partial_period_crossing_base(self):
        # YTD 160_000, period 20_000, base 168_600 → only 8_600 still SS-taxable
        r = employee_fica(
            period_wages=20_000,
            ytd_wages_before=160_000,
            tax_year=2024,
            filing_status='single',
        )
        assert r.ss_taxable_wages == pytest.approx(8_600)
        assert r.social_security == pytest.approx(8_600 * 0.062)
        assert r.medicare == pytest.approx(20_000 * 0.0145)
        # Blended SS rate on the period = 8600/20000 * 6.2%
        assert r.ss_effective_rate == pytest.approx((8_600 * 0.062) / 20_000)

    def test_already_over_base_zero_ss(self):
        r = employee_fica(
            period_wages=37_733,
            ytd_wages_before=180_000,
            tax_year=2024,
            filing_status='single',
        )
        assert r.social_security == pytest.approx(0.0)
        assert r.medicare == pytest.approx(37_733 * 0.0145)
        assert r.ss_exhausted is True

    def test_incremental_additional_medicare_across_threshold(self):
        # YTD 190k, period 20k, thr 200k → Add'l Med on 10k of the period
        r = employee_fica(
            period_wages=20_000,
            ytd_wages_before=190_000,
            tax_year=2024,
            filing_status='single',
        )
        assert r.additional_medicare == pytest.approx(10_000 * 0.009)
        assert r.additional_medicare_taxable_wages == pytest.approx(10_000)


class TestVestFicaConsistency:
    def test_vest_fica_matches_ytd_remaining_not_flat_765(self):
        """
        $37,733 vest with $200k full-year W-2 (includes vest):
        YTD before vest = 162_267; SS only on remaining ~6_333 under 168_600 base.
        """
        vest = 37_733.0
        year_wages = 200_000.0
        prof = {
            'filing_status': 'single',
            'state_code': 'CA',
            'tax_year': 2024,
            'use_bracket_engine': True,
            'use_state_engine': True,
            'other_ordinary_income': year_wages,
            'ytd_wages': year_wages,
            'include_fica': True,
            'ss_wage_base_maxed': False,
            'include_niit': True,
            'other_long_term_gains': 0,
            'other_short_term_gains': 0,
            'amt_credit_carryforward': 0,
            'ca_amt_credit_carryforward': 0,
        }
        r = compute_vest_ordinary_tax(prof, vest, wages_include_this_vest=True, has_vested=True)
        ytd_before = year_wages - vest
        expected = employee_fica(
            period_wages=vest,
            ytd_wages_before=ytd_before,
            tax_year=2024,
            filing_status='single',
        )
        assert r['social_security_tax'] == pytest.approx(expected.social_security, rel=1e-6)
        assert r['medicare_tax'] == pytest.approx(expected.medicare, rel=1e-6)
        assert r['additional_medicare_tax'] == pytest.approx(expected.additional_medicare, rel=1e-6)
        # Must NOT be flat 6.2% on full vest
        assert r['social_security_tax'] < vest * 0.062 - 1.0
        # Must NOT be zero if remaining base exists
        assert r['social_security_tax'] > 0
        # Effective all-in on vest should be well below old flat 46.75%
        assert r['effective_rate'] < 0.42

    def test_vest_ss_zero_when_base_already_cleared(self):
        vest = 37_733.0
        prof = {
            'filing_status': 'single',
            'state_code': 'CA',
            'tax_year': 2024,
            'use_bracket_engine': True,
            'use_state_engine': True,
            'other_ordinary_income': 250_000,
            'ytd_wages': 250_000,
            'include_fica': True,
            'ss_wage_base_maxed': False,
            'include_niit': True,
            'other_long_term_gains': 0,
            'other_short_term_gains': 0,
            'amt_credit_carryforward': 0,
            'ca_amt_credit_carryforward': 0,
        }
        r = compute_vest_ordinary_tax(prof, vest, wages_include_this_vest=True, has_vested=True)
        # YTD before vest = 212267 > 168600
        assert r['social_security_tax'] == pytest.approx(0.0)
        assert r['medicare_tax'] == pytest.approx(vest * 0.0145)

    def test_future_vest_does_not_peel_salary(self):
        """Future vest must stack on salary — not treat salary as if it already includes the vest."""
        vest = 40_000.0
        salary = 160_000.0
        prof = {
            'filing_status': 'single',
            'state_code': 'CA',
            'tax_year': 2025,
            'use_bracket_engine': True,
            'use_state_engine': True,
            'other_ordinary_income': salary,
            'ytd_wages': salary,
            'include_fica': True,
            'ss_wage_base_maxed': False,
            'include_niit': True,
            'other_long_term_gains': 0,
            'other_short_term_gains': 0,
            'amt_credit_carryforward': 0,
            'ca_amt_credit_carryforward': 0,
        }
        r = compute_vest_ordinary_tax(prof, vest, wages_include_this_vest=False, has_vested=False)
        assert r['base_ordinary'] == pytest.approx(salary)
        expected = employee_fica(
            period_wages=vest,
            ytd_wages_before=salary,
            tax_year=2025,
            filing_status='single',
        )
        assert r['social_security_tax'] == pytest.approx(expected.social_security)
        # Remaining base 176100-160000=16100
        assert r['social_security_tax'] == pytest.approx(16_100 * 0.062)


class TestFullYearW2Fica:
    def test_200k_2024_matches_module(self):
        y = compute_w2_year_tax(
            tax_year=2024,
            wages=200_000,
            filing_status='single',
            state_code='CA',
            include_fica=True,
        )
        f = employee_fica_full_year(annual_wages=200_000, tax_year=2024, filing_status='single')
        assert y.social_security == pytest.approx(f.social_security)
        assert y.medicare == pytest.approx(f.medicare)
        assert y.additional_medicare == pytest.approx(f.additional_medicare)
        assert y.total_fica == pytest.approx(f.total)

    def test_maxed_flag_ignored_on_full_year_under_base(self):
        y = compute_w2_year_tax(
            tax_year=2024,
            wages=100_000,
            filing_status='single',
            state_code='CA',
            include_fica=True,
            ss_wage_base_maxed=True,
        )
        # Still charge SS when wages under base
        assert y.social_security == pytest.approx(100_000 * 0.062)

    def test_maxed_flag_never_zeros_full_year_ss_over_base(self):
        """
        User bug: $180k 2024 showed SS $0 + Medicare $2,610 when maxed was checked.
        Full-year must still charge SS on the wage base ($168,600 × 6.2%).
        """
        y = compute_w2_year_tax(
            tax_year=2024,
            wages=180_000,
            filing_status='single',
            state_code='CA',
            include_fica=True,
            ss_wage_base_maxed=True,
        )
        assert y.social_security == pytest.approx(168_600 * 0.062)
        assert y.medicare == pytest.approx(180_000 * 0.0145)
        assert y.additional_medicare == pytest.approx(0.0)
        assert y.total_fica == pytest.approx(168_600 * 0.062 + 180_000 * 0.0145)
        # Must not match the broken Medicare-only total
        assert y.total_fica != pytest.approx(2_610.0)

    def test_180k_2024_smartasset_style_stack(self):
        """Rough SmartAsset-style stack: fed + CA + full FICA on $180k single 2024."""
        y = compute_w2_year_tax(
            tax_year=2024,
            wages=180_000,
            filing_status='single',
            state_code='CA',
            include_fica=True,
            ss_wage_base_maxed=False,
        )
        # FICA textbook
        assert y.total_fica == pytest.approx(10_453.20 + 2_610.00, abs=0.05)
        # Income tax alone was what user saw (~$45.6k); all-in should be ~$10k higher
        assert y.income_tax_total == pytest.approx(32_738.5 + 12_877.63, abs=1.0)
        assert y.total_tax == pytest.approx(y.income_tax_total + y.total_fica + y.sdi, abs=0.05)
        assert y.sdi == pytest.approx(180_000 * 0.011)
        assert y.effective_rate > 0.30  # not the broken ~26.8% Medicare-only all-in


class TestSalePathFicaNoDoublePeel:
    def test_sale_equity_stacks_on_profile_wages(self):
        """Profile $150k + $20k equity ordinary: SS on min(20k, remaining)."""
        profile = {
            'tax_year': 2024,
            'filing_status': 'single',
            'other_ordinary_income': 150_000,
            'ytd_wages': 150_000,
            'include_fica': True,
            'ss_wage_base_maxed': False,
        }
        c = compute_fica_components(20_000, profile)
        # Remaining base 168600-150000=18600 → only $18,600 of the $20k still SS-taxable
        assert c['social_security'] == pytest.approx(18_600 * 0.062)
        assert c['ytd_for_fica'] == pytest.approx(150_000)
        assert c['ss_taxable_wages'] == pytest.approx(18_600)
