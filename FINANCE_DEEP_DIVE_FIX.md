# Finance Deep Dive Page Fix - Dec 24, 2025

## Problem Summary
The Finance Deep Dive page (`/grants/finance-deep-dive`) was displaying as a blank white page, preventing users from viewing comprehensive tax and capital gains analysis.

## Root Causes Identified

### 1. Application Not Running
- Flask server wasn't started
- Port 5000 was occupied by macOS AirPlay Receiver
- Solution: Started app on port 5001 using `.venv/bin/python main.py`

### 2. Missing Route (`grants.rules`)
- Base template referenced `grants.rules` route that didn't exist
- Caused `BuildError` when trying to render navigation
- Solution: Added route handler in `app/routes/grants.py`

### 3. Missing Template Data (Critical Issue)
The `finance_deep_dive` route was only passing minimal data but the template expected comprehensive financial calculations:

**Missing Variables:**
- `total_shares_held_vested` / `total_shares_held_all`
- `total_cost_basis_vested` / `total_cost_basis_all`
- `total_current_value_vested` / `total_current_value_all`
- `total_unrealized_gain_vested` / `total_unrealized_gain_all`

**Missing Per-Grant Data:**
- `item.shares_held_vested` / `item.shares_held_all`
- `item.cost_basis_vested` / `item.cost_basis_all`
- `item.current_value_vested` / `item.current_value_all`
- `item.unrealized_gain_vested` / `item.unrealized_gain_all`

**Missing Per-Vest-Event Data:**
- `ve_data.has_vested` - Whether vest date has passed
- `ve_data.shares_held` - Shares currently held
- `ve_data.cost_basis` - Original cost basis
- `ve_data.current_value` - Current market value
- `ve_data.unrealized_gain` - Gain/loss if sold today
- `ve_data.days_held` - Holding period
- `ve_data.is_long_term` - Long-term vs short-term capital gains classification

## Changes Made

### 1. Fixed `finance_deep_dive` Route
**File:** `app/routes/grants.py`

Added comprehensive data calculations:
- Calculate total shares held (vested vs all)
- Calculate cost basis using vest date prices
- Calculate current value using latest stock price
- Calculate unrealized gains
- Determine holding periods and LT/ST classification
- Separate calculations for vested shares vs all shares (including future)

### 2. Tax Profile Integration
Already working correctly:
- Fetches user's `UserTaxProfile` from database
- Gets tax rates (federal, state, LTCG) via `get_tax_rates()`
- Passes `use_manual_rates` flag to indicate manual vs automatic calculation
- Defaults to reasonable rates if no profile exists

### 3. Added Missing Route
**File:** `app/routes/grants.py`

```python
@grants_bp.route('/rules')
@login_required
def rules():
    """View vesting rules and configurations."""
    return render_template('grants/rules.html')
```

## Technical Details

### Data Flow
1. Query all grants and vest events for current user
2. Get latest stock price for current value calculations
3. For each grant:
   - Calculate grant-level totals (vested and all)
   - Enrich each vest event with calculated data
4. Sum up to overall totals
5. Get user's tax profile and rates
6. Pass all data to template

### Key Calculations
```python
# For each vest event:
has_vested = ve.vest_date <= today
shares_held = ve.shares_received if has_vested else ve.shares_vested
cost_basis = shares_held * ve.share_price_at_vest
current_value = shares_held * latest_stock_price
unrealized_gain = current_value - cost_basis
days_held = (today - ve.vest_date).days if has_vested else 0
is_long_term = days_held >= 365
```

## Commits
1. **b4fad12** - "Fix Finance Deep Dive page and integrate tax profile"
   - Fixed finance_deep_dive route with all calculations
   - Added grants.rules route
   - Integrated tax profile data

2. **5f619db** - "Register settings blueprint in app initialization"
   - Registered settings blueprint for tax configuration page

## Testing
- App running on http://127.0.0.1:5001
- All navigation links working
- Finance Deep Dive page rendering with data
- Tax rates properly integrated

## Status
✅ **COMPLETE** - All changes committed and pushed to GitHub
