"""Next-dollar ordinary marginal includes FICA, not just fed + CA PIT."""

from app.utils.wage_year_tax import compute_w2_year_tax


def test_combined_marginal_includes_medicare_when_ss_maxed():
    # Wages above 2026 SS base ($184,500) and above add'l Medicare ($200k single)
    r = compute_w2_year_tax(
        tax_year=2026,
        filing_status='single',
        state_code='CA',
        wages=400_000,
        include_fica=True,
    )
    assert r.ordinary_marginal > 0
    assert r.state_marginal > 0
    # SS maxed; Medicare 1.45% + Add'l 0.9% = 2.35%
    assert abs(r.fica_marginal - 0.0235) < 1e-6
    assert abs(
        r.combined_ordinary_marginal
        - (r.ordinary_marginal + r.state_marginal + 0.0235 + 0.011)
    ) < 1e-6


def test_sale_gains_in_year_tax_not_fica():
    """Capital gains raise tax base / effective stack, not the FICA wage base."""
    r = compute_w2_year_tax(
        tax_year=2026,
        filing_status='single',
        state_code='CA',
        wages=200_000,
        stcg=10_000,
        ltcg=40_000,
        include_fica=True,
        fica_wages=200_000,
    )
    assert r.stcg == 10_000
    assert r.ltcg == 40_000
    assert r.fica_wages == 200_000
    assert r.wages == 200_000
    # Effective rate is on wages + gains
    assert r.effective_rate > 0
    assert abs((r.total_tax / 250_000) - r.effective_rate) < 1e-9


def test_stacking_prefers_computed_ordinary():
    from app.utils.tax_engine import stacking_ordinary_income
    assert stacking_ordinary_income({
        'other_ordinary_income': 200_000,
        'ytd_wages': 50_000,
        'computed_ordinary': 350_000,
    }) == 350_000


def test_combined_marginal_includes_ss_below_wage_base():
    r = compute_w2_year_tax(
        tax_year=2026,
        filing_status='single',
        state_code='CA',
        wages=80_000,
        include_fica=True,
    )
    # Below SS base and below add'l Medicare: 6.2% + 1.45% = 7.65%
    assert abs(r.fica_marginal - 0.0765) < 1e-6
    assert r.combined_ordinary_marginal > r.ordinary_marginal + r.state_marginal + 0.07
