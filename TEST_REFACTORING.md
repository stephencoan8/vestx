# Comprehensive Refactoring Test Plan

## What Was Fixed

### 1. **Root Cause: Authentication-Dependent Properties**
**PROBLEM**: Properties like `share_price_at_vest` called `get_latest_user_price()` which requires `current_user.is_authenticated`. When templates called these properties, auth context wasn't guaranteed → prices returned 0.

**SOLUTION**: Created `VestEvent.get_complete_data()` that takes explicit `user_key` parameter, no auth dependency.

### 2. **Wrong Labels**
**PROBLEM**: "Strike Price" shown for ALL grant types (RSUs, ISOs, CASH)

**SOLUTION**: 
- Added `is_iso` flag to vest_data dict
- Template now uses: `{% if vest_data.is_iso %}Strike Price{% else %}Grant Price{% endif %}`
- ISOs show "Strike Price" ✓
- RSUs show "Grant Price" ✓

### 3. **Duplicate Calculations**
**PROBLEM**: Cost basis, tax estimates, share counts calculated separately in:
- finance_deep_dive route (lines 550-640)
- vest_detail route (lines 703-860)
- get_estimated_sale_tax() utility
- Multiple properties in VestEvent model

**SOLUTION**: Single source of truth - `VestEvent.get_complete_data()` calculates everything once, returns comprehensive dict.

### 4. **Properties with Side Effects (N+1 Problem)**
**PROBLEM**: Properties made database queries:
- `share_price_at_vest` → queries UserPrice table
- `value_at_vest` → calls share_price_at_vest property
- `net_value` → calls value_at_vest property
- Called multiple times per page → multiple DB queries

**SOLUTION**: `get_complete_data()` fetches prices ONCE, calculates all values in one pass.

## Files Modified

### 1. app/models/vest_event.py
- **Added**: `get_complete_data()` method (lines 400-631, 231 lines)
- **Returns**: Comprehensive dict with 26 fields
- **Benefits**: 
  * No auth dependencies
  * Single DB query for prices
  * All calculations in one place
  * Works outside request context

### 2. app/routes/grants.py
- **Changed**: vest_detail route (lines 703-778)
- **Before**: 157 lines with duplicate calculations
- **After**: 76 lines calling get_complete_data()
- **Benefits**:
  * Single method call replaces 150+ lines
  * Passes vest_data dict instead of 10+ variables
  * Clean separation: route prepares, template displays

### 3. app/templates/grants/vest_detail.html
- **Changed**: All sections to use vest_data dict
- **Grant Information** (lines 34-40):
  * Before: `Strike Price` for all grants
  * After: `{% if vest_data.is_iso %}Strike Price{% else %}Grant Price{% endif %}`
- **Vest Event Details** (lines 63-70):
  * Before: `{{ vest_event.share_price_at_vest }}` (property call)
  * After: `{{ vest_data.price_at_vest }}` (data display)
- **Tax Breakdown** (lines 96-147):
  * Before: `{{ tax_breakdown }}`
  * After: `{{ vest_data.tax_breakdown }}`
- **Sale Details** (lines 242-300):
  * Before: `{{ estimated_sale_tax }}`
  * After: `{{ vest_data.sale_tax_projection }}`
- **Share Activity** (lines 300-330):
  * Before: `{{ total_sold }}`, `{{ total_exercised }}`, `{{ remaining_shares }}`
  * After: `{{ vest_data.shares_sold }}`, `{{ vest_data.shares_exercised }}`, `{{ vest_data.shares_remaining }}`
- **ISO Exercise Form** (line 425):
  * Before: `{{ grant.share_price_at_grant }}`
  * After: `{{ vest_data.strike_price }}`
- **Sale Form** (line 369):
  * Before: `{{ vest_event.share_price_at_vest }}`
  * After: `{{ vest_data.cost_basis_per_share }}`

### 4. Documentation Created
- COMPREHENSIVE_REFACTOR_PLAN.md: Full architectural analysis
- REFACTOR_vest_detail_route.py: Example simplified route
- REFACTOR_template_guide.md: Template refactoring guide

## Test Cases

### Test 1: Vested RSU - Verify Prices Show Correctly (Not $0)
1. Navigate to a vested RSU vest event
2. **VERIFY**: Price at Vest shows actual price (NOT $0)
3. **VERIFY**: Gross Value = shares_vested × price_at_vest
4. **VERIFY**: Net Value = gross_value - tax_withheld_value
5. **VERIFY**: Label shows "Grant Price" (NOT "Strike Price")

### Test 2: Vested ISO - Verify Labels Correct
1. Navigate to a vested ISO vest event
2. **VERIFY**: Label shows "Strike Price" ✓
3. **VERIFY**: Strike price matches grant.share_price_at_grant
4. **VERIFY**: ISO Exercise form shows correct strike price
5. **VERIFY**: Exercise form max shares = shares_remaining

### Test 3: Unvested Event - Verify Estimated Prices
1. Navigate to an unvested vest event (future date)
2. **VERIFY**: Price at Vest shows latest price with "(estimated)" label
3. **VERIFY**: All calculations use estimated price
4. **VERIFY**: Estimated values are reasonable

### Test 4: Tax Breakdown - Verify Calculations Consistent
1. Navigate to any vest event with tax profile configured
2. **VERIFY**: Tax breakdown shows federal, state, FICA, Medicare, NIIT
3. **VERIFY**: Total tax = sum of all components
4. **VERIFY**: Shares withheld = total_tax / price_at_vest
5. **VERIFY**: Shares received = shares_vested - shares_withheld

### Test 5: Sale Tax Projection - Verify Capital Gains
1. Navigate to vest event with remaining shares
2. **VERIFY**: "Estimated Tax if Sold Today" section appears
3. **VERIFY**: Remaining shares count is correct
4. **VERIFY**: Cost basis = price_at_vest (for RSUs) or strike_price (for ISOs)
5. **VERIFY**: Unrealized gain = (current_price - cost_basis) × shares_remaining
6. **VERIFY**: Holding period calculated correctly
7. **VERIFY**: Long-term vs short-term capital gains rate applied correctly

### Test 6: Share Activity - Verify Counts
1. Record a stock sale for a vest event
2. **VERIFY**: Shares Sold updates correctly
3. **VERIFY**: Shares Remaining = shares_received - shares_sold - shares_exercised
4. **VERIFY**: All share counts add up correctly

### Test 7: ISO Exercise - Verify Form Data
1. Navigate to ISO vest event
2. Click "Record Exercise"
3. **VERIFY**: Max shares = shares_remaining
4. **VERIFY**: Strike price shown in info box is correct
5. **VERIFY**: FMV at Exercise defaults to current price
6. Record exercise
7. **VERIFY**: Shares Exercised updates
8. **VERIFY**: Shares Remaining decreases

### Test 8: Multiple Vests - Verify Consistency
1. Navigate to Finance → Deep Dive
2. **VERIFY**: All vest events show same data as vest_detail page
3. **VERIFY**: Totals add up correctly
4. **VERIFY**: No discrepancies between pages

### Test 9: Performance - Verify No N+1 Queries
1. Enable Flask debug toolbar or SQL logging
2. Navigate to vest_detail page
3. **VERIFY**: Only ONE query to UserPrice table (not one per vest)
4. **VERIFY**: Page loads quickly

### Test 10: Edge Cases
1. **Vest with NO sales/exercises**: Verify shares_remaining = shares_received
2. **Vest with ALL shares sold**: Verify shares_remaining = 0, no "Sell Shares" button
3. **Vest with no tax profile**: Verify simplified tax calculation used
4. **CASH grant**: Verify no strike price shown, correct labels
5. **ISO with AMT**: Verify AMT fields appear in exercise form

## Expected Results

### All Tests Should Pass With:
✅ Prices showing actual values (NOT $0)
✅ Correct labels ("Strike Price" for ISOs, "Grant Price" for RSUs)
✅ Consistent calculations across all pages
✅ Single database query for prices (efficient)
✅ All share counts adding up correctly
✅ Tax projections matching vest tax calculations
✅ No template errors or missing data
✅ Fast page load times (no N+1 queries)

## Known Issues to Monitor

1. **Old Properties Still Exist**: `share_price_at_vest`, `value_at_vest`, `net_value` properties still exist in VestEvent model but are deprecated. They should NOT be used but won't cause errors if accidentally called.

2. **finance_deep_dive Route**: Still uses old calculation pattern. Needs refactoring next.

3. **Other Templates**: Other templates (grants list, finance pages) may still reference old variables. They should be refactored using the same pattern.

## Next Steps After Testing

1. If all tests pass → Apply same pattern to finance_deep_dive route/template
2. Refactor grants list page to use centralized data
3. Remove/deprecate old properties that make DB queries
4. Add automated tests to prevent regression
5. Document vest_data dict structure in API docs

## Rollback Plan (If Tests Fail)

If critical issues found:
```bash
git revert bbfe6ee  # Revert template changes
git revert 43c00ae  # Revert route changes
git revert [commit]  # Revert get_complete_data() method
git push --force
```

Then debug individual pieces before re-applying.

## Success Criteria

✅ All 10 test cases pass
✅ No $0 prices showing
✅ Correct labels on all grant types
✅ Consistent data across all pages
✅ Single source of truth working
✅ Performance improved (fewer DB queries)
✅ Code is cleaner and more maintainable

**If all criteria met → REFACTORING SUCCESS! Apply pattern to remaining pages.**
