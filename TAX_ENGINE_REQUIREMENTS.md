# Industry-Standard Tax Calculation Engine - Requirements

## Current State Analysis

### What We Have ✅
1. **Basic Vest Taxation:**
   - Federal/State marginal rates
   - FICA (Social Security + Medicare) with wage cap
   - Additional Medicare Tax (0.9% over threshold)
   - Progressive tax bracket lookup by year

2. **Capital Gains (Basic):**
   - Long-term vs Short-term distinction (1-year holding)
   - Basic LTCG/STCG rate application
   - NIIT (3.8% Net Investment Income Tax) on high earners

3. **User Tax Profiles:**
   - Filing status (Single, Married Filing Jointly)
   - State selection
   - Annual income tracking
   - Manual rate override option

### Critical Gaps 🚨

#### 1. **NO Alternative Minimum Tax (AMT) - CRITICAL FOR ISOs**
- ISOs trigger AMT on exercise (spread = FMV - strike price)
- AMT runs parallel tax calculation, you pay higher of regular vs AMT
- AMT credit can carry forward to future years
- **This is the #1 issue for ISO holders - without it, projections are WRONG**

#### 2. **NO ISO Disqualifying Disposition Rules**
- Qualifying disposition: Hold 2 years from grant + 1 year from exercise → favorable LTCG treatment
- Disqualifying disposition: Sell before holding requirements → ordinary income on bargain element
- **Missing this means ISO tax projections are fundamentally broken**

#### 3. **NO ESPP Qualified vs Disqualifying Logic**
- Qualified: Hold 2 years from offering + 1 year from purchase → favorable treatment
- Disqualifying: Sell before requirements → ordinary income on discount
- **ESPP tax calculations are incomplete without this**

#### 4. **NO Section 83(b) Election Tracking**
- For RSAs (Restricted Stock Awards) - pay tax at grant on value even if unvested
- Affects cost basis and future capital gains
- **If user has RSAs with 83(b) elections, our calculations are wrong**

#### 5. **NO Wash Sale Rules**
- If you sell at a loss and rebuy within 30 days → loss is disallowed
- Cost basis adjustment required
- **Capital loss projections could be overstated**

#### 6. **NO Tax Loss Harvesting Optimization**
- Professional software suggests optimal times to realize losses
- Matches losses against gains to minimize tax
- **Missing opportunity for tax savings**

#### 7. **NO Multi-Year Tax Projection**
- Can't model "what if I sell in 2027 vs 2028"
- Can't project tax brackets based on future income
- **Can't optimize multi-year tax strategy**

#### 8. **NO Scenario Modeling for Future Stock Prices**
- User wants to input: "What if stock hits $200 in 2027?"
- Need multiple price scenarios (base, bull, bear)
- Project tax impact under each scenario
- **This is explicitly requested - user wants to plan with future prices**

#### 9. **Limited Cost Basis Tracking**
- No separate lots for same-day vests with different tax treatment
- No FIFO/LIFO/Specific ID lot selection
- **Cost basis calculations could be wrong for partial sales**

#### 10. **NO State-Specific Rules**
- California has different ESPP treatment
- Some states don't recognize ISO favorable treatment
- **State tax projections could be materially wrong**

---

## Professional Tax Software Framework

### How Schwab/Fidelity/E*TRADE Tax Calculators Work:

#### 1. **Multi-Layered Tax Calculation**
```
Layer 1: Ordinary Income (W-2)
  → Vesting events (RSUs, disqualifying ISO sales, ESPP discount)
  → Progressive federal brackets
  → Progressive state brackets
  → FICA (with SS wage cap)
  → Additional Medicare Tax

Layer 2: Alternative Minimum Tax (AMT)
  → ISO exercise spread added to AMTI
  → AMT exemption & phase-out
  → 26%/28% AMT rates
  → Compare to regular tax, pay higher
  → Track AMT credit for future use

Layer 3: Capital Gains/Losses
  → Short-term (ordinary income rates)
  → Long-term (0%, 15%, 20% based on income)
  → NIIT (3.8% over threshold)
  → State capital gains (some states treat differently)
  → Loss limitations ($3,000/year ordinary income offset)
  → Carryforward tracking

Layer 4: Stock-Specific Rules
  → ISO qualifying holding periods
  → ESPP qualifying disposition rules
  → Wash sale adjustments
  → Section 83(b) elections
  → Specific lot identification
```

#### 2. **Event-Based Tracking**
Each equity event creates a tax "record":
- **Grant:** Record strike price, grant date, election status (83b)
- **Vest:** W-2 income, FMV at vest, cost basis established
- **Exercise (ISO):** AMT adjustment, holding period starts
- **Sale:** Realize gain/loss, check holding periods, apply wash sales

#### 3. **Projection Engine**
- Year-by-year tax projection (not just current year)
- Multiple scenarios with different assumptions
- Optimization suggestions ("sell in Jan vs Dec")
- Bracket management ("you're $5K from next bracket")

---

## What I Need From You

### Immediate Questions:

1. **ISO Grants - Do you have any?**
   - If YES: Do you exercise immediately or hold options?
   - Have you ever triggered AMT?
   - Do you track AMT credit carryforwards?

2. **ESPP - Current treatment?**
   - Are you holding for qualified disposition (2 years)?
   - Or selling immediately (disqualifying)?
   - Do we need to track both scenarios?

3. **RSAs vs RSUs - Which do you have?**
   - RSAs = can elect 83(b) early taxation
   - RSUs = no choice, tax at vest
   - Do any grants have 83(b) elections?

4. **Historical Data:**
   - Do you have prior year tax returns with exact figures?
   - Do you know your AMT history?
   - Do you have cost basis for existing holdings?

5. **Future Projections:**
   - What stock price scenarios do you want to model?
     - Example: Base case ($150), Bull ($250), Bear ($100)
   - What years should we project? (2026-2030? 2026-2035?)
   - Do you want to model salary changes?

6. **State Tax:**
   - Which state are you in?
   - Planning to move states? (changes tax strategy significantly)

7. **Sale Strategy:**
   - Do you want to model:
     - Systematic selling (X shares per quarter)?
     - Tax-optimized selling (minimize tax each year)?
     - Bracket-aware selling (stay under certain brackets)?
     - Scenario comparison tool?

### Data I Need to Build Accurate Engine:

#### For Each Vest Event (Past):
- Exact FMV at vest date
- Actual tax withheld (federal, state, FICA)
- Shares withheld vs cash paid
- Any shares sold to cover taxes

#### For Each Sale (Past):
- Sale date, shares sold, price
- Cost basis (what vest event did shares come from)
- Actual gains/losses realized
- Any wash sales that occurred

#### For Future Projections:
- Expected annual salary (non-stock income)
- Expected stock price path (or multiple scenarios)
- Tax filing status (any changes expected?)
- State residency (any moves planned?)

---

## Proposed Implementation Plan

### Phase 1: Fix Core Tax Engine (2-3 days)
**Goal:** Accurate tax on vesting events and sales

1. **Build Professional Tax Calculator:**
   - Separate modules: OrdinaryIncome, AMT, CapitalGains, StateTax
   - Year-specific tax tables (2024, 2025, 2026+)
   - Proper bracket application (not just marginal rate)

2. **ISO Handling:**
   - Track exercise events separately from vest
   - Calculate AMT adjustment on exercise
   - Monitor holding periods for qualification
   - Handle disqualifying dispositions correctly

3. **ESPP Fixes:**
   - Discount taxed as ordinary income (always)
   - Track offering date, purchase date, sale date
   - Apply qualified/disqualifying rules correctly

4. **Cost Basis Tracking:**
   - Lot-level tracking (each vest = separate lot)
   - Adjustments for tax withholding shares
   - FIFO/LIFO/SpecID support

### Phase 2: Future Price Scenarios (1-2 days)
**Goal:** Model "what if stock price is X in year Y"

1. **Price Projection System:**
   - Allow multiple named scenarios ("Base", "Bull", "Bear", "Custom")
   - Input future prices by date or formula
   - Apply to all unvested grants
   - Recalculate future tax impact

2. **Scenario Comparison:**
   - Side-by-side tax comparison
   - "Sell now vs hold 1 year" calculator
   - Visual charts showing tax over time

### Phase 3: Multi-Year Optimization (2-3 days)
**Goal:** Answer "when should I sell to minimize taxes"

1. **Year-by-Year Projection:**
   - Project income & tax brackets for 5-10 years
   - Model vesting schedule impact on income
   - Show AMT exposure by year

2. **Sale Optimization:**
   - Suggest optimal sale timing
   - Bracket management alerts
   - LTCG vs STCG trade-off calculator

3. **Tax Loss Harvesting:**
   - Identify loss harvesting opportunities
   - Wash sale warnings
   - Loss carryforward tracking

### Phase 4: Professional Reports (1 day)
**Goal:** Match Fidelity/Schwab report quality

1. **Tax Summary Report:**
   - Year-end tax estimate (1040 preview)
   - Breakdown by income type
   - AMT calculation detail
   - Capital gains schedule

2. **Cost Basis Report:**
   - All lots with acquisition date, basis, current value
   - Gain/loss if sold today
   - Holding period status

3. **Multi-Year Projection:**
   - 5-year tax forecast
   - Scenario comparison table
   - Optimization recommendations

---

## Benchmarking Criteria

To match world-class software, we must:

✅ **Accuracy:**
- Tax calculations within $100 of actual tax owed
- Proper AMT calculation (if applicable)
- Correct holding period determinations
- State tax matches actual return

✅ **Completeness:**
- All equity compensation types supported
- All tax jurisdictions handled
- Multi-year projections available
- Scenario modeling functional

✅ **Intelligence:**
- Suggests tax-optimal strategies
- Warns about tax events (AMT, bracket jumps)
- Shows opportunity cost of decisions
- Compares alternatives side-by-side

✅ **Transparency:**
- Shows exactly how tax was calculated
- Cites tax code sections
- Explains AMT, ISO rules, etc.
- Provides audit trail

---

## Next Steps

**You tell me:**
1. Which ISO/ESPP/RSA questions above apply to you?
2. What historical data do you have available?
3. What future scenarios do you want to model?
4. What's your #1 tax concern? (AMT? High LTCG? Bracket management?)

**I'll build:**
1. Professional-grade tax engine matching industry standards
2. Future price scenario modeling
3. Multi-year tax optimization
4. Whatever else you need to beat benchmark software

**Timeline:**
- If you answer questions today → Engine built in 3-5 days
- Full system with optimization → 7-10 days
- Benchmark-ready → Ready when you are

Let's do this right. No shortcuts.
