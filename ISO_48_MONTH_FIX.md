# ISO 48-Month Vesting Fix

## Issue
ISO grants were incorrectly vesting over 60 months (5 years) instead of 48 months (4 years).

## Root Cause
The vest_calculator.py had hardcoded `VESTING_MONTHS = 60` for both ISO 5Y and ISO 6Y, which was incorrect.

## Correct SpaceX ISO Vesting Logic

### ISO 5Y (5-year grant)
- **Grant**: Year 0
- **Vesting starts**: 1 year after grant (Year 1)
- **Cliff**: 1.5 years after grant (6 months into vesting)
- **Vesting ends**: 5 years after grant (Year 5)
- **Total vesting period**: 4 years = **48 months**

### ISO 6Y (6-year grant)
- **Grant**: Year 0
- **Vesting starts**: 2 years after grant (Year 2)
- **Cliff**: 2.5 years after grant (6 months into vesting)
- **Vesting ends**: 6 years after grant (Year 6)
- **Total vesting period**: 4 years = **48 months**

## Vesting Pattern (Both Types)
- **First vest at cliff**: 6/48 of total shares (12.5%)
- **Monthly thereafter**: 1/48 of total shares (2.08%) for 42 more months
- **Total vests**: 43 (1 cliff + 42 monthly)

## Example: 1,122 Shares (ISO 6Y)
- **First vest**: 140.25 shares (6/48 × 1,122)
- **Monthly**: 23.38 shares (1/48 × 1,122)
- **Total**: 140.25 + (42 × 23.38) = 1,122 shares ✅

## Changes Made

### 1. vest_calculator.py
```python
# Before (WRONG):
VESTING_MONTHS = 60  # Both ISO 5Y and 6Y vest over 5 years

# After (CORRECT):
VESTING_MONTHS = 48  # Both ISO 5Y and 6Y vest over 4 years
```

### 2. Database Migration
Created and ran `fix_iso_48_months.py` to:
- Delete old vest events for all ISO grants
- Recalculate using correct 48-month logic
- Create new vest events with proper dates and share amounts

### 3. Test Suite
Updated `test_iso_vesting.py` to expect:
- 43 vest events (was 55)
- 6/48 first vest (was 6/60)
- 1/48 monthly vests (was 1/60)
- Correct end dates

## Impact

### Before Fix (60 months)
- First vest: 112.20 shares (6/60 × 1,122)
- Monthly: 18.70 shares (1/60 × 1,122)
- Total vests: 55
- End date: 2031-01-01

### After Fix (48 months)
- First vest: 140.25 shares (6/48 × 1,122) ✅
- Monthly: 23.38 shares (1/48 × 1,122) ✅
- Total vests: 43
- End date: 2030-01-01

## Verification
All tests pass:
```
✅ First vest amount correct (6/48 = 12.5%)
✅ Monthly vest amount correct (1/48 = 2.08%)
✅ Total vests correct (43 events)
✅ Total shares match grant (100.00)
```

## Commit
- Hash: 436568b
- Message: "Fix ISO vesting to 48 months (4 years) for both ISO 5Y and 6Y"
- Pushed to: main branch
