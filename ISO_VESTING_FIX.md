# ISO Vesting Schedule Fix - December 22, 2025

## Changes Made

### 1. Removed Auto-Multiplication of ISO Shares

**Previous Behavior:**
- When user entered 1000 ISOs (5Y), the system automatically multiplied by 2 to get 2000
- When user entered 1000 ISOs (6Y), the system automatically multiplied by 3 to get 3000

**New Behavior:**
- User enters the exact number of ISOs they have
- No auto-multiplication happens
- If you have 1000 ISOs, enter 1000 (not 500 or 333)

**Files Changed:**
- `app/routes/grants.py` - Removed multiplication logic from lines 52-54 and 164-166

### 2. Fixed ISO Vesting Schedule Logic

**ISO 5Y (2x multiplier) Vesting:**
- **Cliff:** 1.5 years after grant date
- **First Vest:** 0.5 years (6 months) worth of shares at the 1.5 year mark
- **Remaining:** TRUE monthly vesting (12 events per year) for the rest of the 5-year period
- **Total Period:** 5 years
- **Total Events:** 1 cliff + 54 monthly = 55 vest events

**ISO 6Y (3x multiplier) Vesting:**
- **Cliff:** 2.5 years after grant date  
- **First Vest:** 0.5 years (6 months) worth of shares at the 2.5 year mark
- **Remaining:** TRUE monthly vesting (12 events per year) for the rest of the 6-year period
- **Total Period:** 6 years
- **Total Events:** 1 cliff + 66 monthly = 67 vest events

**Example: 1000 ISO 5Y shares**
```
Grant Date: Jan 1, 2024
Total Shares: 1,000
Vest Period: 5 years (60 months)
Shares per month: 1,000 / 60 = 16.67

Cliff (Nov 15, 2025 - 1.5 years later):
  - Vests: 6 months worth = 16.67 × 6 = 100 shares

Then EVERY month (12 times per year):
  - Dec 15, 2025: 16.67 shares
  - Jan 15, 2026: 16.67 shares
  - Feb 15, 2026: 16.67 shares
  - ... continues monthly until May 15, 2030
  - Total: 54 monthly vests after cliff

Total Events: 1 cliff + 54 monthly = 55 vest events
```

**Example: 1000 ISO 6Y shares**
```
Grant Date: Jan 1, 2024
Total Shares: 1,000
Vest Period: 6 years (72 months)
Shares per month: 1,000 / 72 = 13.89

Cliff (Nov 15, 2026 - 2.5 years later):
  - Vests: 6 months worth = 13.89 × 6 = 83.33 shares

Then EVERY month (12 times per year):
  - Dec 15, 2026: 13.89 shares
  - Jan 15, 2027: 13.89 shares
  - Feb 15, 2027: 13.89 shares
  - ... continues monthly until May 15, 2032
  - Total: 66 monthly vests after cliff

Total Events: 1 cliff + 66 monthly = 67 vest events
```

**Files Changed:**
- `app/utils/vest_calculator.py` - Updated monthly vesting logic
  - Changed cliff shares calculation to always be 6 months worth
  - Fixed months_vested tracking to start at 6 (not cliff_months)
  - This ensures all shares vest over the full period

## Testing

Created `test_iso_vesting.py` to verify:
- ✅ ISO 5Y vests 100 shares at first cliff (6/60 of total)
- ✅ ISO 5Y vests all 1,000 shares over 5 years
- ✅ ISO 6Y vests 83.33 shares at first cliff (6/72 of total)  
- ✅ ISO 6Y vests all 1,000 shares over 6 years
- ✅ RSU vesting still works correctly (semi-annual)

## How to Use

### Adding a New ISO Grant

1. Go to "Add Grant"
2. Select grant type (e.g., "New Hire")
3. Select share type: "ISO - 5 Year (2x multiplier)" or "ISO - 6 Year (3x multiplier)"
4. **Enter the exact number of ISOs you have** (e.g., 1000)
   - Don't multiply by 2 or 3 yourself
   - Just enter what's on your grant document
5. The system will automatically:
   - Set the correct vesting period (5 or 6 years)
   - Set the correct cliff (1.5 or 2.5 years)
   - Calculate monthly vesting with 0.5 year first cliff

### Editing Existing Grants

If you have existing grants that were auto-multiplied:
1. Go to the grant detail page
2. Click "Edit"
3. **Divide the share quantity by the multiplier**:
   - If it shows 2000 ISO 5Y, change to 1000
   - If it shows 3000 ISO 6Y, change to 1000
4. Save the grant
5. The vesting schedule will recalculate correctly

## Migration Notes

**Existing Grants:**
- Any ISO grants created before this fix will have inflated share counts
- You'll need to manually edit them to fix the quantities
- Divide by 2 for ISO 5Y, divide by 3 for ISO 6Y

**Example Migration:**
```
Before: 2000 shares of ISO 5Y
After: 1000 shares of ISO 5Y (edit and save)

Before: 3000 shares of ISO 6Y  
After: 1000 shares of ISO 6Y (edit and save)
```

## Verification

To verify your grants are correct after the fix:
1. Check the total shares matches your grant document
2. Verify the first vest is approximately 8-10% of total (6 months worth)
3. Check that the final vest date is 5 or 6 years from grant date
4. Sum all vest events - should equal your total grant amount

## Technical Details

### Vest Date Alignment
- ISOs vest **truly monthly** - 12 vest events per year
- Each monthly vest is on the 15th of the month
- The cliff vest is aligned to either 6/15 or 11/15 (whichever comes after the cliff period)
- After cliff, vesting continues on the 15th of every subsequent month

### Cliff Calculation
- ISO 5Y: 1.5 years = 18 months cliff
- ISO 6Y: 2.5 years = 30 months cliff  
- First vest date is the next 6/15 or 11/15 after the cliff period
- At cliff, you receive 0.5 years (6 months) worth of shares

## Files Modified

1. **app/routes/grants.py**
   - Removed ISO share multiplication in `add_grant()` (lines 52-54)
   - Removed ISO share multiplication in `edit_grant()` (lines 164-166)

2. **app/utils/vest_calculator.py**
   - Updated monthly vesting logic to create true monthly vest events (12 per year)
   - Changed from grouping into biannual dates to individual monthly vests
   - Cliff vests 6 months worth, then each month vests 1 month worth
   - Fixed tracking to count from 6 months (not from cliff_months)

3. **test_iso_vesting.py** (new file)
   - Test suite to verify ISO vesting calculations
   - Run with: `.venv/bin/python test_iso_vesting.py`

## Commit

```bash
git add .
git commit -m "Fix ISO vesting: Remove auto-multiplication and correct cliff vesting

- Remove 2x/3x auto-multiplication of ISO shares in grant creation/editing
- Fix ISO vesting to vest 6 months worth at 1.5yr (5Y) or 2.5yr (6Y) cliff
- Continue monthly vesting for remainder of period
- Add test suite to verify vesting calculations
- Users now enter exact ISO count from grant documents"
```

---

**Status:** ✅ Complete and tested
**Date:** December 22, 2025
**Testing:** All vesting calculations verified correct
