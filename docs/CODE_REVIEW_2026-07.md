# VestX multi-agent code review (July 2026)

Excellence-first review. Four specialist agents + orchestrator synthesis.  
No rush mandate — prioritize correctness and product density over shipping speed.

---

## Agent org chart

```
┌─────────────────────────────────────────────────────────────┐
│  Orchestrator (this session)                                │
│  Synthesize · implement high-ROI · document roadmap         │
└────────────┬───────────────────────────┬────────────────────┘
             │                           │
   ┌─────────▼─────────┐       ┌─────────▼─────────┐
   │ TAX ENGINE LEAD   │       │ TAX PROFILE UX    │
   │ payroll · W-2 ·   │       │ cockpit IA · fold │
   │ vest · sale · AMT │       │ value density     │
   └─────────┬─────────┘       └─────────┬─────────┘
             │                           │
   ┌─────────▼─────────┐       ┌─────────▼─────────┐
   │ GRANTS / EQUITY   │       │ PLATFORM / SEC    │
   │ finance dual math │       │ auth · migrates   │
   │ flat leftovers    │       │ tax_center size   │
   └───────────────────┘       └───────────────────┘
             │
   ┌─────────▼─────────┐
   │ VALIDATION (FICA) │  already PASS 14–16 tests
   └───────────────────┘
```

---

## Executive summary

| Area | Grade | Headline |
|------|-------|----------|
| FICA module (`payroll_tax`) | A | Pub 15–style SS remaining base; full-year SS zero bug fixed |
| W-2 year tax | A− | Brackets + std ded + FICA aligned |
| Vest ordinary tax | B+ | Engine path; peel/stack heuristics need multi-vest YTD |
| Sale tax (Tax Center) | B+ | Incremental engine; year profile now wired on APIs |
| Sale tax (Grants/Finance) | D | **Still flat User rates + 15% LTCG** — dual system |
| Tax profile UX | B (after cockpit) | Was warehouse; redesigned to hub-style KPIs |
| Platform security | C | Weak password policy, default admin, no rate limit |
| Schema | C | Boot-time `migrate_*.py` soup; needs Alembic |

**Excellence gap is not “missing math” — it is parallel surfaces that disagree.**

---

## Tax architecture (target SSOT)

| Domain | Owner |
|--------|--------|
| Employee FICA | `app/utils/payroll_tax.py` |
| Federal ordinary / LTCG / sale incremental | `app/utils/tax_engine.py` |
| Full-year W-2 | `app/utils/wage_year_tax.py` |
| CA PIT + MHST | `app/utils/state_tax/` |
| AMT + credits | `app/utils/amt.py` |
| Year inputs | `TaxYearProfile` → `resolve_engine_profile_for_year` |
| Active mirror | `TaxProfile` (sales default year only) |

**Do not fork formulas into** `User.get_tax_rates`, `get_estimated_sale_tax` flat path, or Finance client sliders.

---

## Findings by severity

### P0 — dual tax systems / year bleed

1. **Grants Finance + vest sale estimates** use flat `User` rates and 15% LTCG; Tax Center uses `analyze_sales`.  
2. **Finance client JS** re-multiplies flat rates and can overwrite server engine numbers.  
3. ~~Analyze/goal/sales used active `TaxProfile` wages for any year~~ → **fixed** via `_engine_profile_for_request`.

### P1 — engine / table hygiene

4. ~~AMT `generate_credit_from_amt=… or True` always true~~ → **fixed**.  
5. ~~LTCG brackets only 2025–26~~ → **2023–24 added**.  
6. Sale path still no federal/CA std deduction (vest/W-2 have it) — residual cliff risk.  
7. Multi-vest SS ordering not sequential.  
8. `tax_center.py` ~1.2k LOC god module.

### P2 — platform / dead code

9. Default admin `admin/admin`; password validator min length 1.  
10. No rate limiter despite 429 handler.  
11. Tracebacks in API JSON when not DEBUG.  
12. Broken `transactions` blueprint; legacy sale-planning still alive.  
13. Migrate-script proliferation.

---

## What shipped in this review pass

1. **Tax profile cockpit UX** — KPI strip, 2-column inputs/composition, collapsed extras, equity chip, less prose.  
2. **Year-scoped engine** on analyze / goal / record sale / record exercise.  
3. **AMT credit generation** only with ISO bargain.  
4. **LTCG tables** for 2023–2024.  
5. This document.

---

## Roadmap (ordered excellence)

| PR | Theme | Avoid mixing |
|----|--------|--------------|
| **A** | Unify Grants sale tax → `analyze_sales` + kill flat Finance sliders | Security |
| **B** | Std ded on `_federal_state_layer` base/full | UI redesign |
| **C** | Split `tax_center` into profile / engine_api / activity / advisor | Math |
| **D** | Alembic baseline; retire `migrate_*.py` | Features |
| **E** | Security: password policy, production boot guards, limiter, no traceback | Tax |
| **F** | Delete transactions orphan + sale-planning-legacy | Engine |
| **G** | Multi-vest chronological FICA YTD | Cosmetics |

---

## Tax profile UX principles (locked)

1. **One accent number:** total tax.  
2. **Box 1 is the hero input.**  
3. **Composition, not a second 7-card hero.**  
4. **Vest history is evidence** (collapsed).  
5. **YTD / AMT / engines / overrides** live in details.  
6. **Copy is short** — no textbook on the fold.

---

## Validation

- `pytest tests/test_payroll_tax.py` — FICA golden cases (incl. $180k maxed-flag fix).  
- Manual: Tax profile year switch + live recalc after cockpit deploy.

---

*Generated from multi-agent review; implement residual P0 Grants dual-path next for product integrity.*
