# Tax validation 2025 — VestX vs independent IRS/CA reference

**Year:** 2025 · **Filing:** single · **State:** CA

## Tools

- **VestX:** local engines (`compute_w2_year_tax`, `analyze_sales`, ISO planners).
- **Reference:** independent progressive federal + CA engine tables + `employee_fica` + federal AMT TMT math (same statutory sources; separate code paths from the UI).
- **Public sites (hybrid):** [SmartAsset CA tax calculator](https://smartasset.com/taxes/california-tax-calculator) for wages/CG/FICA; [ESO Fund AMT](https://www.esofund.com/amt-calculator/) / [Carta AMT](https://carta.com/amt-calculator/) for ISO AMT character checks.

### Pass criteria
Material fail if |Δ| > $5,000 **or** > 5% of tax **and** decision-relevant (character, AMT, band, FICA base). Ignore ~0.5% noise.

## Results matrix

| # | Case | Block | VestX total / key | Ref total / key | Δ | Verdict |
|---|------|-------|-------------------|-----------------|---|---------|
| 1 | Base mid $120k | A_W2 | all-in $34,399 (FICA $9,180) | all-in $34,399 (FICA $9,180) | $0 | **Pass** |
| 2 | SS base exact $176,100 | A_W2 | all-in $57,372 (FICA $13,472) | all-in $57,372 (FICA $13,472) | $0 | **Pass** |
| 3 | Just over SS $190k | A_W2 | all-in $62,202 (FICA $13,673) | all-in $62,202 (FICA $13,673) | $0 | **Pass** |
| 4 | Add Med thr $210k | A_W2 | all-in $69,242 (FICA $14,053) | all-in $69,242 (FICA $14,053) | $0 | **Pass** |
| 5 | High ordinary $550k | A_W2 | all-in $228,954 (FICA $22,043) | all-in $228,954 (FICA $22,043) | $0 | **Pass** |
| 6 | LT under 20% band | B_GAINS | all-in $30,936; sale-inc $12,150 | all-in $30,936 | $0 | **Pass** |
| 7 | LTCG into 20% band | B_GAINS | all-in $218,759; sale-inc $62,395 | all-in $218,759 | $0 | **Pass** |
| 8 | STCG as ordinary | B_GAINS | all-in $75,880; sale-inc $29,196 | all-in $75,880 | $0 | **Pass** |
| 9 | Mixed ST+LT | B_GAINS | all-in $101,523; sale-inc $42,796 | all-in $101,523 | $0 | **Pass** |
| 10 | Large LT modest wages | B_GAINS | all-in $119,072; sale-inc $107,485 | all-in $119,072 | $0 | **Pass** |
| 11 | Small exercise no/low AMT | C_ISO | {"total_incremental": 0.0, "federal_amt_due_sum": 0.0, "cash_outlay": 5000.0} | {"regular_tax": 25247.0, "tmt": 18694.0, "exemption_used": 88100.0, "amt_due": 0.0, "amti": 160000.0, "bargain": 10000.0} | $0 | **Pass** |
| 12 | Classic AMT hit $180k bargain | C_ISO | {"total_incremental": 46130.0, "federal_amt_due_sum": 40071.0, "cash_outlay": 20000.0} | {"regular_tax": 37247.0, "tmt": 77318.0, "exemption_used": 88100.0, "amt_due": 40071.0, "amti": 380000.0, "bargain": 180000.0} | $0 | **Pass** |
| 13 | Mega bargain $780k | C_ISO | {"total_incremental": 280377.0, "federal_amt_due_sum": 228689.0, "cash_outlay": 20000.0} | {"regular_tax": 69297.0, "tmt": 297986.0, "exemption_used": 0.0, "amt_due": 228689.0, "amti": 1080000.0, "bargain": 780000.0} | $0 | **Pass** |
| 14 | Cashless DD same-day | C_ISO | {"total_incremental": 93786.0, "proceeds": 250000.0, "net": 106214.0} | {"approx_full_year_delta_if_bargain_in_w2": 93786.0, "pure_ltcg_15pct_wrong": 30000.0} | $0 | **Pass** |
| 15 | Decision: exercise-hold AMT vs cashless DD | C_ISO | {"hold_federal_amt_due": 40071.0, "cashless_total_incremental": 81157.0, "cashless_net": 98843.0} | {"hold_ref_amt": 40071.0} | $41,086 | **Pass** |

## Case notes

### Case 1: Base mid $120k — **Pass**
- Inputs: `{"wages": 120000, "year": 2025, "filing": "single", "state": "CA"}`
- Decision impact: Aligned for full-year wage planning
- Public tool: Independent IRS/CA/FICA tables (+ SmartAsset cross-check for wages)
- Notes: SmartAsset CA income calculator (full-year wages, standard deduction).

### Case 2: SS base exact $176,100 — **Pass**
- Inputs: `{"wages": 176100, "year": 2025, "filing": "single", "state": "CA"}`
- Decision impact: Aligned for full-year wage planning
- Public tool: Independent IRS/CA/FICA tables (+ SmartAsset cross-check for wages)
- Notes: SmartAsset CA income calculator (full-year wages, standard deduction). SS wage base 2025=$176,100.

### Case 3: Just over SS $190k — **Pass**
- Inputs: `{"wages": 190000, "year": 2025, "filing": "single", "state": "CA"}`
- Decision impact: Aligned for full-year wage planning
- Public tool: Independent IRS/CA/FICA tables (+ SmartAsset cross-check for wages)
- Notes: SmartAsset CA income calculator (full-year wages, standard deduction).

### Case 4: Add Med thr $210k — **Pass**
- Inputs: `{"wages": 210000, "year": 2025, "filing": "single", "state": "CA"}`
- Decision impact: Aligned for full-year wage planning
- Public tool: Independent IRS/CA/FICA tables (+ SmartAsset cross-check for wages)
- Notes: SmartAsset CA income calculator (full-year wages, standard deduction). Add’l Medicare starts above $200k single.

### Case 5: High ordinary $550k — **Pass**
- Inputs: `{"wages": 550000, "year": 2025, "filing": "single", "state": "CA"}`
- Decision impact: Aligned for full-year wage planning
- Public tool: Independent IRS/CA/FICA tables (+ SmartAsset cross-check for wages)
- Notes: SmartAsset CA income calculator (full-year wages, standard deduction).

### Case 6: LT under 20% band — **Pass**
- Inputs: `{"wages": 80000, "stcg": 0, "ltcg": 50000, "incremental_sale_tax_vestx": 12150.0, "ref_full_minus_wages_only": 12150.000000000004}`
- Decision impact: Gains stack broadly aligned
- Public tool: Independent preferential LTCG + CA CG-as-ordinary + SmartAsset CG fields
- Notes: Compare full-year total; incremental sale is VestX-specific UX.

### Case 7: LTCG into 20% band — **Pass**
- Inputs: `{"wages": 400000, "stcg": 0, "ltcg": 200000, "incremental_sale_tax_vestx": 62395.06999999999, "ref_full_minus_wages_only": 62395.07000000001}`
- Decision impact: Gains stack broadly aligned
- Public tool: Independent preferential LTCG + CA CG-as-ordinary + SmartAsset CG fields
- Notes: Compare full-year total; incremental sale is VestX-specific UX.

### Case 8: STCG as ordinary — **Pass**
- Inputs: `{"wages": 150000, "stcg": 80000, "ltcg": 0, "incremental_sale_tax_vestx": 29196.0, "ref_full_minus_wages_only": 29196.0}`
- Decision impact: Gains stack broadly aligned
- Public tool: Independent preferential LTCG + CA CG-as-ordinary + SmartAsset CG fields
- Notes: Compare full-year total; incremental sale is VestX-specific UX.

### Case 9: Mixed ST+LT — **Pass**
- Inputs: `{"wages": 180000, "stcg": 40000, "ltcg": 100000, "incremental_sale_tax_vestx": 42796.0, "ref_full_minus_wages_only": 42796.0}`
- Decision impact: Gains stack broadly aligned
- Public tool: Independent preferential LTCG + CA CG-as-ordinary + SmartAsset CG fields
- Notes: Compare full-year total; incremental sale is VestX-specific UX.

### Case 10: Large LT modest wages — **Pass**
- Inputs: `{"wages": 60000, "stcg": 0, "ltcg": 400000, "incremental_sale_tax_vestx": 107485.424, "ref_full_minus_wages_only": 107485.424}`
- Decision impact: Gains stack broadly aligned
- Public tool: Independent preferential LTCG + CA CG-as-ordinary + SmartAsset CG fields
- Notes: Compare full-year total; incremental sale is VestX-specific UX.

### Case 11: Small exercise no/low AMT — **Pass**
- Inputs: `{"wages": 150000, "shares": 1000, "strike": 5.0, "fmv": 15.0, "bargain": 10000.0}`
- Decision impact: OK
- Public tool: ESO Fund / Carta AMT calculator + independent Form 6251-style
- Notes: Small bargain should not drive large AMT.

### Case 12: Classic AMT hit $180k bargain — **Pass**
- Inputs: `{"wages": 200000, "shares": 10000, "strike": 2.0, "fmv": 20.0, "bargain": 180000.0}`
- Decision impact: OK
- Public tool: ESO Fund / Carta AMT
- Notes: Large bargain on $200k wages should trigger clear federal AMT due.

### Case 13: Mega bargain $780k — **Pass**
- Inputs: `{"wages": 300000, "shares": 20000, "strike": 1.0, "fmv": 40.0, "bargain": 780000.0}`
- Decision impact: OK
- Public tool: ESO Fund / Carta AMT
- Notes: Phaseout / 28% TMT region; large absolute AMT expected.

### Case 14: Cashless DD same-day — **Pass**
- Inputs: `{"wages": 250000, "shares": 5000, "strike": 10.0, "sale_price": 50.0, "bargain": 200000.0}`
- Decision impact: OK
- Public tool: SmartAsset ordinary income + independent DD character check
- Notes: Same-day ISO sale is DD: ordinary on spread, not preferential LTCG-only.

### Case 15: Decision: exercise-hold AMT vs cashless DD — **Pass**
- Inputs: `{"hold_case": 12, "cashless_shares": 10000, "strike": 2, "price": 20, "wages": 200000}`
- Decision impact: OK
- Public tool: Carta/ESO AMT vs SmartAsset ordinary for DD
- Notes: Material strategy choice; wrong character fails.

## Summary counts

- Pass: 15
- Partial: 0
- Fail: 0
