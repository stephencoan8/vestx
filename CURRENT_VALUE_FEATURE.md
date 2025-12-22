# Current Value Feature

## Summary
Added dynamic current value calculation to the grants list and detail views so users can see both historical and current values of their grants.

## Changes Made

### 1. Grant Model (`app/models/grant.py`)
Added two new `@property` methods:
- `current_share_price`: Gets the latest stock price from the stock_prices table
- `current_value`: Calculates total current value (quantity × current price)

These properties dynamically calculate values based on the latest stock price, ensuring values are always up-to-date without needing to refresh or recreate data.

### 2. Grants List Template (`app/templates/grants/list.html`)
Updated the table to show:
- **Price at Grant**: Historical price when grant was awarded
- **Value at Grant**: Historical total value (quantity × price at grant)
- **Current Price**: Latest stock price (highlighted)
- **Current Value**: Current total value (quantity × current price, highlighted)

### 3. Grant Detail View (`app/templates/grants/view.html`)
Updated the grant information section to show:
- **Price at Grant**: Historical price when grant was awarded
- **Value at Grant**: Historical total value
- **Current Share Price**: Latest stock price (highlighted)
- **Current Total Value**: Current total value (highlighted)

## How It Works

1. **Dynamic Calculation**: Both current values are calculated on-the-fly using `@property` methods in the Grant model
2. **Stock Price Source**: Uses `get_latest_stock_price()` from `app.utils.init_db` to fetch the most recent stock price
3. **Automatic Updates**: When you update stock prices in the admin panel, all grant current values update immediately - no manual refresh needed
4. **Historical Preservation**: The original grant values are preserved for comparison, showing how much the grant has appreciated

## Example

If you have a grant with:
- 1,949 shares
- Grant price: $77.00
- Current price: $421.00

The display shows:
- **Value at Grant**: $150,073.00
- **Current Value**: $820,529.00

This shows a 5.47x appreciation in value!

## Benefits

1. **Real-time visibility**: See current grant values without manual calculation
2. **Appreciation tracking**: Compare historical vs current values to see growth
3. **Dynamic updates**: Values update automatically when stock prices change
4. **Consistent with vest events**: Uses the same dynamic calculation pattern as vest event values
