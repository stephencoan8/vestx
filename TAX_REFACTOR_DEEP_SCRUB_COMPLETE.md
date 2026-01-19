# Tax System Refactor - Deep Scrub Complete

## Date: January 19, 2026

## Objective
Remove ALL legacy tax logic from VestX and fully implement the simplified tax profile system where users select their own tax rates.

---

## Issues Found During Deep Scrub

### 1. **Missing Helper Methods in User Model**
**Error**: `'User' object has no attribute 'get_federal_tax_rate'`

**Root Cause**: The refactored code called `user.get_federal_tax_rate()` and `user.get_state_tax_rate()`, but these methods didn't exist in the User model.

**Fix**: Added two helper methods to `app/models/user.py`:
```python
def get_federal_tax_rate(self) -> float:
    """Get user's federal tax rate (defaults to 22% if not set)."""
    return self.federal_tax_rate if self.federal_tax_rate is not None else 0.22

def get_state_tax_rate(self) -> float:
    """Get user's state tax rate (defaults to 0% if not set)."""
    return self.state_tax_rate if self.state_tax_rate is not None else 0.0
```

### 2. **Missing Rate Fields in Tax Breakdown**
**Error**: `'dict object' has no attribute 'social_security_rate'`

**Root Cause**: Templates (`finance_deep_dive.html`, `vest_detail.html`) expected rate fields to display percentages to users, but the new simplified tax breakdown only included tax amounts, not the rates.

**Fix**: Updated `vest_event.get_comprehensive_tax_breakdown()` to include:
- `social_security_rate`: 0.062 (6.2%) or 0.0 if FICA disabled
- `medicare_rate`: 0.0145 (1.45%) or 0.0 if FICA disabled
- `additional_medicare_rate`: 0.009 (0.9%) if applicable, 0.0 otherwise

---

## Complete List of Files Modified

### Core Tax Calculation Logic
1. **`app/models/vest_event.py`**
   - ✅ Refactored `get_comprehensive_tax_breakdown()` - removed UserTaxProfile, AnnualIncome, TaxCalculator
   - ✅ Refactored `get_unrealized_position()` - removed CapitalGainsCalculator
   - ✅ Added rate fields to tax breakdown for template compatibility

2. **`app/models/stock_sale.py`**
   - ✅ Refactored `get_estimated_tax()` - removed UserTaxProfile, AnnualIncome, CapitalGainsCalculator
   - ✅ Uses simplified capital gains rates: 15% LTCG, user's federal rate for STCG

3. **`app/models/user.py`**
   - ✅ Added `get_federal_tax_rate()` helper method
   - ✅ Added `get_state_tax_rate()` helper method
   - ✅ Updated `get_tax_rates()` to use new helpers

### Routes
4. **`app/routes/grants.py`**
   - ✅ Removed UserTaxProfile and AnnualIncome imports
   - ✅ Removed tax_profile and annual_incomes queries from `vest_detail` route
   - ✅ Simplified call to `vest_event.get_complete_data()`

---

## New Simplified Tax Framework

### User Tax Profile (Settings)
Users configure their tax preferences in `/settings/profile`:
- **Federal Tax Rate**: User selects their marginal federal bracket (10%, 12%, 22%, 24%, 32%, 35%, 37%)
- **State Tax Rate**: User enters their state tax rate (e.g., 9.3% for CA)
- **Include FICA**: Toggle to include/exclude FICA in estimates

### Tax Calculations

#### 1. Vest Tax Withholding
```python
federal_tax = gross_value * user.get_federal_tax_rate()
state_tax = gross_value * user.get_state_tax_rate()

if user.include_fica:
    ss_tax = min(gross_value, 168600) * 0.062  # SS cap
    medicare_tax = gross_value * 0.0145
    additional_medicare = max(0, gross_value - 200000) * 0.009
else:
    ss_tax = medicare_tax = additional_medicare = 0

total_tax = federal_tax + state_tax + ss_tax + medicare_tax + additional_medicare
```

#### 2. Capital Gains (Unrealized Positions & Sales)
```python
if is_long_term:
    federal_rate = 0.15  # Standard LTCG rate
else:
    federal_rate = user.get_federal_tax_rate()  # STCG = ordinary income

state_rate = user.get_state_tax_rate()

# NIIT for high earners (simplified)
if user.get_federal_tax_rate() >= 0.32:
    niit = capital_gain * 0.038
else:
    niit = 0

total_tax = (capital_gain * federal_rate) + (capital_gain * state_rate) + niit
```

---

## Legacy Components Removed

### Completely Removed from Active Code Paths:
- ❌ `UserTaxProfile` model queries
- ❌ `AnnualIncome` model queries
- ❌ `TaxBracket` model queries
- ❌ `TaxCalculator` class usage
- ❌ `CapitalGainsCalculator` class usage
- ❌ Year-by-year income tracking
- ❌ Marginal bracket calculations
- ❌ Progressive tax rate computations
- ❌ YTD wages tracking
- ❌ Effective vs marginal rate logic

### Still Exist But Unused:
These models/files still exist in the codebase but are no longer used in active code paths:
- `app/models/tax_rate.py` (contains TaxBracket, UserTaxProfile)
- `app/models/annual_income.py` (contains AnnualIncome)
- `app/utils/tax_calculator.py` (contains TaxCalculator)
- `app/utils/capital_gains_calculator.py` (contains CapitalGainsCalculator)

**Recommendation**: These can be safely deleted in a future cleanup, but keeping them for now ensures backward compatibility if we need to reference old data.

---

## Benefits of New System

### For Users:
1. **Transparent** - Users see exactly what rates are used in calculations
2. **Simple** - No complex income tracking across years
3. **Controllable** - Users set their own rates based on their tax situation
4. **Fast** - No complex progressive calculations or DB queries

### For Code:
1. **Maintainable** - Simple flat rate multiplication, easy to understand
2. **Performant** - No N+1 queries, no complex calculations
3. **Reliable** - Fewer edge cases, fewer failure points
4. **Testable** - Straightforward logic, predictable results

---

## Testing Checklist

- ✅ Finance Deep Dive page loads without errors
- ✅ Vest detail pages show tax breakdowns
- ✅ Tax rates display correctly (percentages)
- ✅ FICA calculations work when enabled/disabled
- ✅ Capital gains estimates use correct rates (LTCG vs STCG)
- ✅ User can configure tax preferences in settings
- ✅ All templates render without UndefinedError

---

## Commits

1. `f3ca373` - Refactor: Remove all legacy tax logic, fully implement simplified tax system
2. `7173f17` - Add get_federal_tax_rate() and get_state_tax_rate() helper methods to User model
3. `77cc23d` - Add missing rate fields to tax breakdown for template compatibility

---

## Status: ✅ COMPLETE

All legacy tax logic has been removed from active code paths. The simplified tax system is fully implemented and working in production. Users now have full control over their tax rate assumptions.

**Next Steps** (Optional Future Work):
1. Consider adding tax rate presets by state (e.g., "California High Earner" = 37% fed + 9.3% state)
2. Add help text explaining tax brackets and FICA
3. Delete unused legacy tax models/files after confirming no data dependencies
