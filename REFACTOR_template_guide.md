# REFACTORED TEMPLATE - Using vest_data centralized dict
# This shows how the template should access data from vest_data instead of calculating

<!-- OLD WAY (scattered, duplicated): -->
<!-- Price at Vest: {{ vest_event.share_price_at_vest }} -->
<!-- This calls a property that makes DB queries -->

<!-- NEW WAY (centralized): -->
<!-- Price at Vest: {{ vest_data.price_at_vest }} -->
<!-- Pure data display, no calculations -->

## Key Template Changes:

### 1. Grant Information Section
OLD:
```html
{% if grant.share_price_at_grant %}
<div class="info-item">
    <label>Strike Price:</label>  <!-- WRONG for RSUs -->
    <span>${{ "%.2f"|format(grant.share_price_at_grant) }}</span>
</div>
{% endif %}
```

NEW:
```html
{% if vest_data.is_iso %}
<div class="info-item">
    <label>Strike Price:</label>  <!-- Only for ISOs -->
    <span>${{ "%.2f"|format(vest_data.strike_price) }}</span>
</div>
{% else %}
<div class="info-item">
    <label>Grant Price:</label>  <!-- For RSUs -->
    <span>${{ "%.2f"|format(grant.share_price_at_grant) }}</span>
</div>
{% endif %}
```

### 2. Vest Event Details
OLD:
```html
<div class="info-item">
    <label>Price at Vest:</label>
    <span>${{ "%.2f"|format(vest_event.share_price_at_vest) }}</span>  <!-- Property call -->
</div>
<div class="info-item">
    <label>Gross Value:</label>
    <span>${{ "{:,.2f}".format(vest_event.value_at_vest) }}</span>  <!-- Property call -->
</div>
<div class="info-item">
    <label>Shares Received:</label>
    <span>{{ "%.4f"|format(vest_event.shares_received) }}</span>  <!-- Property call -->
</div>
```

NEW:
```html
<div class="info-item">
    <label>Price at Vest:</label>
    <span>
        ${{ "%.2f"|format(vest_data.price_at_vest) }}
        {% if not vest_data.has_vested %}<small>(estimated)</small>{% endif %}
    </span>
</div>
<div class="info-item">
    <label>Gross Value:</label>
    <span>${{ "{:,.2f}".format(vest_data.gross_value) }}</span>
</div>
<div class="info-item">
    <label>Shares Received:</label>
    <span>{{ "%.4f"|format(vest_data.shares_received) }}</span>
</div>
```

### 3. Tax Breakdown
OLD:
```html
{% if tax_breakdown and tax_breakdown.has_breakdown %}
<!-- displays tax_breakdown dict -->
{% endif %}
```

NEW:
```html
{% if vest_data.tax_breakdown and vest_data.tax_breakdown.has_breakdown %}
<!-- displays vest_data.tax_breakdown dict -->
{% endif %}
```

### 4. Sale Details
OLD:
```html
{% if estimated_sale_tax and estimated_sale_tax.shares_held > 0 %}
<div>Remaining Shares: {{ "%.4f"|format(estimated_sale_tax.shares_held) }}</div>
<div>Cost Basis: ${{ "%.2f"|format(estimated_sale_tax.cost_basis_per_share) }}</div>
{% endif %}
```

NEW:
```html
{% if vest_data.shares_remaining > 0 and vest_data.sale_tax_projection %}
<div>Remaining Shares: {{ "%.4f"|format(vest_data.shares_remaining) }}</div>
<div>Cost Basis: ${{ "%.2f"|format(vest_data.cost_basis_per_share) }}</div>
<div>Current Value: ${{ "{:,.2f}".format(vest_data.current_market_value) }}</div>
<div>Unrealized Gain: ${{ "{:,.2f}".format(vest_data.unrealized_gain) }}</div>
<!-- Tax projection -->
{% with proj = vest_data.sale_tax_projection %}
<div>Estimated Tax: ${{ "{:,.2f}".format(proj.estimated_tax) }}</div>
{% endwith %}
{% endif %}
```

### 5. Share Activity
OLD:
```html
<div>Shares Received: {{ "%.4f"|format(vest_event.shares_received) }}</div>
<div>Shares Sold: {{ "%.4f"|format(total_sold) }}</div>
<div>Shares Exercised: {{ "%.4f"|format(total_exercised) }}</div>
<div>Shares Remaining: {{ "%.4f"|format(remaining_shares) }}</div>
```

NEW:
```html
<div>Shares Received: {{ "%.4f"|format(vest_data.shares_received) }}</div>
<div>Shares Sold: {{ "%.4f"|format(vest_data.shares_sold) }}</div>
<div>Shares Exercised: {{ "%.4f"|format(vest_data.shares_exercised) }}</div>
<div>Shares Remaining: {{ "%.4f"|format(vest_data.shares_remaining) }}</div>
```

### 6. ISO Exercise Form
OLD:
```html
<div>
    <strong>Strike Price:</strong> ${{ "%.2f"|format(grant.share_price_at_grant) }}
</div>
```

NEW:
```html
{% if vest_data.is_iso %}
<div>
    <strong>Strike Price:</strong> ${{ "%.2f"|format(vest_data.strike_price) }}
</div>
{% endif %}
```

## Summary:
- ALL data comes from vest_data dict
- NO calculations in template
- NO property calls (vest_event.share_price_at_vest, etc.)
- Proper conditional labels (Strike Price for ISOs only)
- All values pre-calculated in backend
