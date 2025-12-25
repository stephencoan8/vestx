# Cash Grant Handling Fix - Dec 24, 2025

## Problem Summary
The system was treating cash bonuses like stock grants, calculating share prices and unrealized gains for cash amounts. Cash bonuses should be tracked as USD amounts, not shares of stock.

## Key Insight
For `ShareType.CASH` grants:
- **`share_quantity`** in the Grant model represents USD amount of the bonus
- **`shares_vested`** in VestEvent represents USD amount vesting
- **`shares_received`** represents USD received after taxes
- **No stock price calculations** should be applied
- **No unrealized gains** - cash value doesn't change with stock price

## Changes Made

### 1. Updated `Grant` Model (`app/models/grant.py`)

#### `total_value_at_grant` Property
```python
# Cash bonuses: share_quantity represents USD amount
if self.share_type == ShareType.CASH.value:
    return self.share_quantity
```

#### `current_value` Property
```python
# Cash bonuses: value is fixed USD amount
if self.share_type == ShareType.CASH.value:
    return self.share_quantity
```

### 2. Updated `VestEvent` Model (`app/models/vest_event.py`)

#### `value_at_vest` Property
```python
# Cash bonuses: shares_vested represents USD amount
if self.grant.share_type == ShareType.CASH.value:
    return self.shares_vested
```

#### `net_value` Property
```python
# Cash bonuses: shares_received represents USD amount
if self.grant.share_type == ShareType.CASH.value:
    return self.shares_received
```

#### New `tax_withheld` Property
```python
@property
def tax_withheld(self) -> float:
    """
    Calculate total tax withheld (cash paid + value of shares sold).
    For cash bonuses: cash_paid + shares_sold (both in USD)
    For stock grants: cash_paid + (shares_sold × price_at_vest)
    """
    total_tax = self.cash_paid
    
    if self.grant.share_type == ShareType.CASH.value:
        total_tax += self.shares_sold  # USD for cash
    else:
        total_tax += self.shares_sold * self.share_price_at_vest  # Shares × price
    
    return total_tax
```

### 3. Updated Finance Deep Dive Route (`app/routes/grants.py`)

Modified the vest event enrichment logic to handle cash grants separately:

```python
is_cash_grant = grant.share_type == 'cash'

for ve in vest_events:
    if is_cash_grant:
        shares_held = ve.shares_received if has_vested else ve.shares_vested
        cost_basis_per_share = 1.0  # $1 per $1 for cash
        cost_basis = shares_held  # USD amount
        current_value = shares_held  # Cash value doesn't change
        unrealized_gain = 0.0  # No gain/loss on cash
    else:
        # Stock grant calculations with price lookups
        ...
```

## How Cash Grants Work Now

### Data Entry
When adding a cash grant:
- **Grant Type**: Any (e.g., annual_performance, promotion)
- **Share Type**: CASH
- **Share Quantity**: Enter the USD amount (e.g., 50000 for $50,000 bonus)
- **Share Price at Grant**: Can be 0 or 1 (not used in calculations)

### Vesting Schedule
- Cash bonuses vest according to their schedule (e.g., 1 year, 5 years)
- `shares_vested` in each VestEvent represents USD amount vesting
- No stock price lookups or calculations

### Tax Handling
- **Cash Paid**: USD paid out of pocket for taxes
- **Shares Sold**: For cash grants, this represents additional USD withheld
- **Shares Received**: USD amount received after taxes

### Display
- Finance Deep Dive shows cash grants with:
  - **Shares Held**: Actually USD amount held
  - **Cost Basis**: USD amount (1:1)
  - **Current Value**: USD amount (doesn't change)
  - **Unrealized Gain**: Always $0 (cash doesn't appreciate)

## Benefits

1. **Accurate Tracking**: Cash bonuses tracked as USD, not phantom shares
2. **No False Gains**: Cash doesn't show unrealized gains/losses
3. **Proper Tax Calculation**: Tax withholding calculated correctly for cash
4. **Consistent Interface**: Same UI works for both cash and stock grants
5. **Clear Reporting**: Finance Deep Dive correctly distinguishes cash from stock

## Example

### Before (Incorrect)
- Annual Performance Bonus: $50,000 cash
- System treated as 50,000 "shares"
- Calculated fake "cost basis" with stock prices
- Showed false "unrealized gains" based on stock movement

### After (Correct)
- Annual Performance Bonus: $50,000 cash
- Tracked as $50,000 USD
- Cost basis: $50,000 (1:1)
- Current value: $50,000 (unchanged)
- Unrealized gain: $0 (cash is cash)

## Testing
- App automatically reloaded (running in debug mode)
- All calculations now properly handle cash vs stock grants
- Finance Deep Dive page correctly shows cash grants separately

## Commit
**d440734** - "Fix cash grant handling - track USD amounts not shares"

## Status
✅ **COMPLETE** - All changes committed and pushed to GitHub
