"""
Pinned tax-year constants with citations.

Bump this file for a new calendar year — do not hunt through engines.
Planning-grade; not a filed return.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# API / engine stamp (kept in tax_center.ADVISOR_API_VERSION too)
TAX_TABLE_VERSION = '2026-08-29-v6-withholding'

# --- Federal standard deduction (IRS inflation Rev. Proc.) ---
FED_STD_DEDUCTION: Dict[int, Dict[str, float]] = {
    2023: {'single': 13850, 'mfj': 27700, 'mfs': 13850, 'hoh': 20800},
    2024: {'single': 14600, 'mfj': 29200, 'mfs': 14600, 'hoh': 21900},
    2025: {'single': 15000, 'mfj': 30000, 'mfs': 15000, 'hoh': 22500},
    2026: {'single': 16100, 'mfj': 32200, 'mfs': 16100, 'hoh': 24150},
}

# --- CA standard deduction (FTB Form 540-ES) ---
# 2026 single $5,706 is the 540-ES figure (same as 2025 ES), not a 2.7% inflate.
CA_STD_DEDUCTION: Dict[int, Dict[str, float]] = {
    2023: {'single': 5202, 'mfj': 10404, 'mfs': 5202, 'hoh': 10404},
    2024: {'single': 5540, 'mfj': 11080, 'mfs': 5540, 'hoh': 11080},
    2025: {'single': 5706, 'mfj': 11412, 'mfs': 5706, 'hoh': 11412},
    2026: {'single': 5706, 'mfj': 11412, 'mfs': 5706, 'hoh': 11412},
}
CA_STD_SOURCE = {
    2025: 'FTB Form 540-ES 2025',
    2026: 'FTB Form 540-ES 2026 (single $5,706)',
}

# --- Employee payroll ---
# SSA OASDI wage base; IRS Pub 15 Additional Medicare; EDD CA SDI 2026
SS_WAGE_BASE: Dict[int, float] = {
    2022: 147_000.0,
    2023: 160_200.0,
    2024: 168_600.0,
    2025: 176_100.0,
    2026: 184_500.0,
}
SS_EMPLOYEE_RATE = 0.062
MEDICARE_EMPLOYEE_RATE = 0.0145
ADDITIONAL_MEDICARE_RATE = 0.009
CA_SDI_RATE = 0.013  # EDD 2026, no wage cap
CA_SDI_SOURCE = 'EDD 2026 CA SDI employee rate 1.3%, uncapped'

# --- Supplemental withholding (paycheck on RSU / bonus) ---
# IRS Pub 15-A: 22% optional flat on supplemental; 37% once YTD supplemental > $1M
FED_SUPP_RATE = 0.22
FED_SUPP_RATE_HIGH = 0.37
FED_SUPP_HIGH_THRESHOLD = 1_000_000.0
# CA DE 4 / EDD supplemental: 10.23% (2024–2026 planning)
CA_SUPP_RATE = 0.1023
CA_SUPP_SOURCE = 'EDD / FTB supplemental wage withholding 10.23%'

# --- Federal AMT 28% TMT breakpoint (taxable AMTI after exemption) ---
# 2026: $244,500 (not the 2025 ~$220,700 table)
FED_AMT_28_THRESHOLD: Dict[int, Dict[str, float]] = {
    2025: {'single': 220700, 'hoh': 220700, 'mfj': 220700, 'mfs': 110350},
    2026: {'single': 244500, 'hoh': 244500, 'mfj': 244500, 'mfs': 122250},
}

# --- CA 540-ES installment fractions (of annual CA estimate) ---
# Q1 30% Apr 15, Q2 40% Jun 15, Q3 0% Sep 15, Q4 30% Jan 15
CA_ES_FRACTIONS: List[float] = [0.30, 0.40, 0.00, 0.30]
CA_ES_SOURCE = 'FTB Form 540-ES 30% / 40% / 0% / 30%'

# SALT cap (planning): 2026 OBBBA phase-down ~$14k vs $16,100 std
SALT_CAP: Dict[int, float] = {
    2025: 10_000.0,
    2026: 14_000.0,
}

# ESPP §423
ESPP_ANNUAL_LIMIT = 25_000.0  # FMV at grant / offering, per calendar year

CITATIONS: Dict[str, str] = {
    'fed_std_2026': 'IRS inflation-adjusted standard deduction 2026',
    'ca_std_2026': CA_STD_SOURCE[2026],
    'ss_base_2026': 'SSA OASDI wage base 2026 $184,500',
    'add_medicare': 'IRC §3101(b)(2) / IRS Topic 560',
    'ca_sdi_2026': CA_SDI_SOURCE,
    'fed_supp': 'IRS Pub 15-A supplemental 22% / 37% over $1M',
    'ca_supp': CA_SUPP_SOURCE,
    'ca_es': CA_ES_SOURCE,
    'amt_28_2026': 'Form 6251 26%/28% breakpoint $244,500 (2026)',
    'safe_harbor': 'IRC §6654: 90% current or 100%/110% prior-year tax (AGI > $150k → 110%)',
}


def std_for(table: dict, year: int, filing: str) -> float:
    years = sorted(table.keys())
    use = year if year in table else min(years, key=lambda y: abs(y - year))
    row = table[use]
    return float(row.get(filing) or row.get('single') or 0)


def amt_28_threshold(year: int, filing: str) -> float:
    years = sorted(FED_AMT_28_THRESHOLD.keys())
    use = year if year in FED_AMT_28_THRESHOLD else min(years, key=lambda y: abs(y - year))
    row = FED_AMT_28_THRESHOLD[use]
    filing = filing if filing in row else 'single'
    return float(row[filing])
