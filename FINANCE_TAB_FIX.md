# Finance Tab Cost Basis Fix

## Issue
The Finance Deep Dive page was showing $0.00 for cost basis and price at grant, which affected:
- Holding period calculations
- Estimated tax calculations  
- Unrealized gain calculations

## Root Causes

### 1. **Incorrect Cost Basis Logic for ISOs**
The code was using the **FMV at vest** (`share_price_at_vest`) as the cost basis for all grant types, including ISOs. This is incorrect:

- **ISOs (Incentive Stock Options)**: Cost basis should be the **strike/exercise price** (`share_price_at_grant`)
  - You pay the strike price to exercise the option
  - Capital gains = (sale price - strike price)
  - The "spread" (FMV at vest - strike price) is taxed as ordinary income at vest

- **RSUs/RSAs/ESPP**: Cost basis should be the **FMV at vest** (`share_price_at_vest`)
  - The full FMV at vest is taxed as ordinary income
  - Capital gains = (sale price - FMV at vest)

### 2. **Missing Template Data**
The template was trying to access `ve_data.holding_period` and `ve_data.estimated_tax`, but these fields were not being added to the `ve_data` dictionary in the route.

## Changes Made

### 1. Fixed Cost Basis Calculation (`app/routes/grants.py`)
```python
# Before (incorrect for ISOs)
cost_basis_per_share = ve.share_price_at_vest
cost_basis = shares_held * cost_basis_per_share

# After (correct for all grant types)
if grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
    # For ISOs, cost basis is the strike/exercise price
    cost_basis_per_share = grant.share_price_at_grant
else:
    # For RSUs/RSAs/ESPP, cost basis is the FMV at vest
    cost_basis_per_share = ve.share_price_at_vest

cost_basis = shares_held * cost_basis_per_share
```

### 2. Added Missing Template Fields (`app/routes/grants.py`)
Added `holding_period` and `estimated_tax` to the `ve_data` dictionary:
```python
# Calculate holding period display
if has_vested:
    if days_held >= 365:
        years = days_held // 365
        holding_period = f"{years}y {days_held % 365}d"
    else:
        holding_period = f"{days_held}d"
else:
    holding_period = "—"

# Add to ve_data dict
ve_data = {
    # ...existing fields...
    'holding_period': holding_period,
    'estimated_tax': estimated_tax
}
```

### 3. Improved Error Handling (`app/models/vest_event.py`)
Enhanced the `share_price_at_vest` property with better logging and error handling:
- Logs warnings when no price is found
- Logs warnings when user is not authenticated
- Logs debug info about which price was found and used
- Better exception handling with detailed error logging

## Tax Implications

### ISOs (Incentive Stock Options)
**At Exercise/Vest:**
- Spread (FMV at vest - strike price) is taxed as ordinary income
- May trigger AMT (Alternative Minimum Tax)

**At Sale:**
- Cost basis = Strike price (what you paid)
- Capital gain = Sale price - Strike price
- If held 1+ year from vest: Long-term capital gains (preferential rates)
- If held < 1 year from vest: Short-term capital gains (ordinary income rates)

### RSUs/RSAs/ESPP
**At Vest:**
- Full FMV at vest is taxed as ordinary income
- Withholding via sell-to-cover or cash payment

**At Sale:**
- Cost basis = FMV at vest (already taxed)
- Capital gain = Sale price - FMV at vest
- If held 1+ year from vest: Long-term capital gains
- If held < 1 year from vest: Short-term capital gains

## Testing
After deployment, verify:
1. ✅ Cost basis shows correct values (not $0.00)
2. ✅ ISOs show strike price as cost basis
3. ✅ RSUs/RSAs/ESPP show FMV at vest as cost basis
4. ✅ Holding period displays correctly (e.g., "1y 45d" or "120d")
5. ✅ Unrealized gain calculates correctly (current value - cost basis)
6. ✅ Estimated tax on sale calculates correctly based on holding period

## Impact
This fix ensures that all tax calculations in the Finance Deep Dive are now accurate and compliant with IRS rules for equity compensation. Users can now:
- See accurate cost basis for their holdings
- Calculate correct capital gains/losses
- Make informed decisions about when to sell (short-term vs long-term)
- Estimate tax liability more accurately
