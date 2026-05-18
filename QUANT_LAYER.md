# Quant Layer — Full Breakdown

---

## 1. Entry Logic

### Current behavior
- Monthly 3rd Friday expiry target
- Entry date = target DTE days before expiry, ±3 day fallback for holidays
- Option selected by closest delta to target, scored by delta distance + DTE distance
- Mid price entry: (bid + ask) / 2

### Issues

**Delta band is fixed ±0.08 regardless of context**
- A ±0.08 band at 30 DTE behaves differently than at 60 DTE
- In high-IV months the chain is wider — we may skip better strikes
- Fix: tighten band at 30 DTE (±0.05), widen at 60 DTE (±0.12)

**No liquidity filter beyond ask > 0**
- Could select options with $0.01 bid / $0.50 ask (50% spread)
- Real fills on these would be far worse than mid price
- Fix: minimum bid >= 0.10, max spread % = (ask-bid)/mid < 0.30

**Mid price assumes perfect fills**
- Selling at mid is optimistic on sell orders
- More realistic: entry price = bid + 20% of spread
- Impact: reduces reported premium by ~5-15% depending on liquidity

**No open interest or volume filter**
- Low OI options have wide spreads and poor price discovery
- Fix: minimum open_interest >= 100 at entry

**Entry timing is always monthly (3rd Friday cycle)**
- Weekly options exist and are more flexible
- Some strategies benefit from shorter DTE entries in high-IV
- Fix (later): optional weekly entry mode

---

## 2. Exit Logic

### Current behavior
- 4 scenarios: exit25, exit50, exitExp, roll20
- EOD marks only
- roll15 disabled (curve inflation bug)

### Issues

**No stop loss scenario — biggest gap**
- All catastrophic losses in the research came from no stop
- Industry standard: close if mark >= 2× premium collected
- Fix: add c_stop2x check in daily loop — "close if current value doubles entry credit"
- Also consider: 3x stop for wider strategies

**EOD marks only**
- 50% target hit intraday → recorded as next-day close
- Overstates days_held by ~0.5-1 day
- Not solvable without tick data — document as known limitation

**roll20 hidden from grid**
- Exists as scenario, excluded from grid comparisons
- Fix: add roll20 to grid_scenarios (one line)

**No roll-down / roll-up scenario**
- When a leg is breached, real traders roll to further strike
- Fix (medium): add roll_breach scenario — roll when mark >= 1.5× premium

**No DTE-based profit taking**
- Some traders use: close at 50% profit OR 21 DTE, whichever comes first
- Combines time and profit exit in one rule
- Fix: add exit50_or_21dte scenario

---

## 3. P&L and Equity Curve

### Current behavior
- CC: compounded curve using spy_entry × 100 as denominator
- CSP: compounded curve using strike × 100 as denominator
- Wheel: cumulative dollar P&L only
- Yearly P&L breakdown per scenario

### Issues

**Wheel has no % equity curve**
- Cannot compare wheel vs CC/CSP on same scale
- Fix: use first PUT strike × 100 as initial capital base

**No annualized return (CAGR)**
- Total return over 3 years ≠ total return over 8 years
- Fix: CAGR = (final_curve / 100) ^ (1 / years) - 1

**Capital efficiency not shown**
- CC ties up ~$45k per contract (100 SPY shares)
- CSP ties up strike × 100 in cash
- Users don't see how much capital is required
- Fix: add capital_required = avg(spy_entry × 100) for CC, avg(strike × 100) for CSP

**Return on capital not shown**
- Fix: return_on_capital = total_pnl / capital_required

**No benchmark comparison**
- Users can't see if CC/CSP/Wheel beat simply buying and holding SPY
- Fix: fetch SPY buy-and-hold return for same date range via yfinance
- Show as overlay on equity curve: "SPY +47% vs Strategy +62%"

**No monthly return distribution**
- Mean/std of monthly returns — shows consistency
- Fix: return distribution histogram data in stats

---

## 4. Statistics — Current Gaps

### Currently computed
- num_trades, total_pnl, avg_premium, avg_pnl, avg_days_held
- win_rate, total_return, max_drawdown, sharpe, yearly_pnl
- CSP: assignment_rate, num_assigned, avg_cost_basis_discount

### Missing stats

**Profit factor**
- gross_wins / abs(gross_losses)
- A profit factor < 1.0 means strategy is losing money despite high win rate
- Easy: one line calculation

**Average win vs average loss**
- Win rate alone is meaningless without this
- 90% win rate + avg win $200 + avg loss $5000 = net loser
- Easy: mean(pnl for pnl > 0) and mean(pnl for pnl < 0)

**Win/loss ratio**
- avg_win / abs(avg_loss)
- Shows payoff asymmetry directly

**Calmar ratio**
- annualized_return / abs(max_drawdown)
- Better than Sharpe for options (non-normal distributions)
- Sharpe penalizes upside volatility; Calmar only penalizes drawdown

**Max consecutive losses**
- Critical for psychological sizing and circuit breaker design
- Easy: scan trade list for longest losing streak

**Max dollar drawdown**
- Current drawdown is % based
- Users need: "at worst I lost $X in dollar terms"
- Easy: peak dollar P&L − trough

**Sharpe with risk-free rate**
- Currently risk_free = 0
- At 4-5% rates this materially inflates Sharpe
- Fix: subtract risk_free/12 from each monthly return

**Expected value per trade**
- EV = (win_rate × avg_win) + ((1 - win_rate) × avg_loss)
- Shows whether the strategy has positive expectancy
- Easy calculation, high value for users

**Theta capture rate**
- How much of the theoretical theta decay the strategy actually captured
- Requires theta at entry from DB
- Shows execution efficiency: did we get the decay we expected?

**Premium capture %**
- avg_pnl / avg_premium_usd × 100
- "On average we kept 68% of the premium collected"
- Very intuitive stat for options traders

---

## 5. Greeks Layer — Currently Missing Entirely

### What we have
- Delta at entry (used for selection only)
- IV at entry (used for filtering)

### What we're missing

**Theta at entry**
- How much time decay per day at entry
- Users want to know: "I collected $4.00 and decay is $0.18/day"
- Already in DB (theta column in fact_option_eod)
- Easy: pull theta from entry row, add to trade log

**Gamma at entry**
- Rate of change of delta — shows how fast delta accelerates near expiry
- High gamma = position gets dangerous fast near expiration
- Already in DB

**Vega at entry**
- Sensitivity to IV changes
- High vega at entry = position is exposed to IV expansion mid-trade
- This directly quantifies the Feb 2020 type risk
- Already in DB

**IV rank / IV percentile at entry**
- Raw IV of 18% means nothing without context
- IV rank = (current IV - 52w low) / (52w high - 52w low)
- IV percentile = % of days in past year where IV was lower
- Requires historical IV data per symbol — computable from DB

**Delta at close**
- When we closed, what was the delta?
- Shows how far ITM/OTM the position was at exit
- Available from EOD data on close date

**Theta decay captured vs theoretical**
- Theoretical: theta_at_entry × days_held
- Actual: premium_collected - close_price
- Ratio shows how much of theoretical decay we actually captured
- Useful for evaluating exit timing

---

## 6. IV Regime Filter

### Current behavior
- Static 18% absolute threshold for all symbols
- Applied at entry only

### Issues

**18% is SPY-specific**
- QQQ baseline IV is ~18-22%, IWM ~20-25%
- 18% cutoff means "Low IV" for QQQ/IWM captures most trades
- Fix: compute per-symbol percentile thresholds from DB
  - Query: SELECT PERCENTILE_CONT(0.33) and (0.67) over historical IV for each symbol
  - Store as symbol config

**IV expansion not tracked during trade**
- The Feb 2020 risk: enter at 15% IV, it hits 80% mid-trade
- No stat currently tracks this
- Fix: add max_iv_during_trade and iv_expansion to trade log
- Requires daily IV scan during trade (already loading EOD data)

**IV percentile not shown at entry**
- Users see raw IV (0.18) but not where it sits historically
- Fix: add iv_percentile_at_entry to trade log

**VIX not used**
- IV of the specific option vs VIX (market-wide fear gauge) are different
- VIX is a cleaner signal for market regime than single-option IV
- Fix: fetch VIX daily from yfinance (free), add vix_at_entry to trade log
- Could add VIX regime filter: Low VIX (<15), Normal (15-25), High (>25)

---

## 7. Wheel Strategy — Deep Issues

### Current behavior
- PUT → assignment → CALL → called away → repeat
- No stop loss, no rolling

### Issues

**Expiry close uses DB mark not intrinsic value (Fix A — already planned)**
- ITM at expiry: should use |underlying - strike|
- OTM at expiry: mark = $0
- Fix requires underlying price on expiry day (already in project)

**CALL delta not adjusted for cost basis distance**
- If SPY is at $500 and cost basis is $420, 17-delta call is very far OTM
- Premium collected is negligible — not worth the trade
- Fix: if (spot - cost_basis) / spot > 0.10, raise target delta to 0.25-0.30

**No wheel % equity curve**
- Cannot benchmark against CC/CSP
- Fix: first PUT strike × 100 as capital base

**No round duration stats**
- Some rounds last 1 month, some 7 years
- Missing: avg_round_duration, longest_round, shortest_round

**Dividend risk not modeled**
- SPY pays quarterly dividends (~1.3% annually)
- Deep ITM calls near ex-dividend date risk early assignment
- Currently ignored entirely
- Fix (later): flag trades within 5 days of SPY ex-dividend date

**No partial assignment modeling**
- Currently: assigned or not (binary)
- Reality: can be partially assigned on larger positions
- Not relevant for 1-contract simulation but worth noting

---

## 8. Missing Strategies — New Ones to Add

### Short Strangle (highest priority)
- The strategy from the research article — we have all the data
- Sell OTM call + OTM put simultaneously
- Both legs managed as one position
- Exit when combined value drops to 50% of total collected
- Why add: directly ties into the research Addy is publishing
- Requires: pull both call and put for same expiry, compute combined mark daily
- New P&L: combined_premium - combined_close_price
- New stats: call_breach_rate, put_breach_rate, leg_that_was_tested

### Iron Condor (natural extension of strangle)
- Short strangle + long wings for defined maximum loss
- Sell 17-delta call/put, buy 5-delta call/put as protection
- Defined risk = (wing_width - net_credit) × 100
- Why add: removes catastrophic loss risk from strangle
- New stat: max_loss_per_trade (defined), risk_reward_ratio

### Short Straddle
- Sell call and put at same strike (ATM)
- Higher premium, zero buffer zone
- More aggressive than strangle
- Simpler to implement (same strike for both legs)

### PMCC — Poor Man's Covered Call
- Buy deep ITM LEAPS (70-80 delta, 12-18 months out)
- Sell short-dated OTM call (30-45 DTE, 30 delta) against it
- Requires much less capital than true covered call (~$5k vs $45k)
- Why add: huge demand from retail traders who can't afford 100 shares of SPY
- New concept: diagonal spread, long leg tracking, net debit basis

### Bull Put Spread (defined-risk CSP)
- Sell OTM put, buy further OTM put as protection
- Defined max loss = (spread_width - net_credit) × 100
- Lower premium but capped downside — more capital efficient
- Why add: beginners want defined risk before going naked

### Cash-Secured Put — Aggressive Version (higher delta)
- Currently only simulates down to 17 delta
- 30-40 delta CSP collects 2-3× more premium, higher assignment rate
- Interesting research angle: what delta maximizes risk-adjusted return?

---

## 9. Position Sizing Layer — Currently Missing

### What we have
- 1 contract per trade always
- No sizing logic whatsoever

### What we need

**Kelly Criterion**
- Optimal fraction of capital to risk per trade
- f = (win_rate × avg_win - (1-win_rate) × abs(avg_loss)) / avg_win
- Half-Kelly is standard for options (full Kelly is too aggressive)
- Would show: "optimal position size = 3.2% of capital per trade"

**Fixed fractional sizing**
- Risk X% of account per trade
- Standard: 1-2% risk per trade
- Show: how many contracts to trade based on account size input

**Max portfolio heat**
- If running multiple positions simultaneously
- % of capital at risk at any given time
- Currently we simulate serial (one position at a time)
- Reality: traders run overlapping monthly positions

---

## 10. Market Regime Layer — Currently Missing

### What we have
- IV regime filter (high/low/all)

### What we need

**VIX regime**
- VIX < 15: calm market (low fear)
- VIX 15-25: normal market
- VIX > 25: stressed market
- Free data from yfinance daily
- Could show: "strategy in VIX > 25 months: 100% win rate"

**Trend regime**
- SPY above/below 200-day SMA at entry
- Trending up: calls are more at risk (CC worse, CSP better)
- Trending down: puts are more at risk (CSP worse)
- Easy: compute from yfinance data already fetched

**Realized vs implied volatility spread**
- IV - HV30 (30-day historical vol)
- When IV >> HV: options are expensive → sell premium
- When IV ≈ HV: options are fair value → neutral
- Premium edge: (IV - HV30) / IV → higher = better entry
- Already computable from DB (IV column) + yfinance (price data for HV)

**Bear market / bull market regime**
- SPY drawdown from ATH > 20% = bear market
- Show strategy performance in bull vs bear regimes separately

---

## 11. Benchmarking Layer — Currently Missing

### What we have
- Equity curve starting at 100 (relative)

### What we need

**Buy and hold SPY**
- Fetch SPY total return for same date range
- Show on same chart as strategy equity curve
- "Strategy: +62% vs SPY Buy and Hold: +47%"

**Treasury bill return**
- Risk-free rate benchmark
- Show: are we beating simply holding T-bills?
- Especially relevant for CSP (cash is sitting in account earning interest)

**Covered call benchmark**
- BXM Index (CBOE Buy-Write Index) is the standard CC benchmark
- Shows how our CC simulation compares to institutional standard

---

## 12. Simulation Accuracy Issues

**Single contract simulation**
- We always simulate 1 contract
- Real traders scale — but P&L scales linearly so not a critical issue

**No slippage model**
- We use mid price at EOD
- Real fills have slippage, especially on exit
- Conservative estimate: $0.02/share slippage per leg
- Impact: reduces P&L by ~$4 per round trip per contract

**No commission model**
- Real fills cost ~$0.65/contract per leg (Tastytrade)
- ~$1.30 per round trip
- Over 206 trades = ~$268 total drag
- Small but worth showing as a deduction option

**American-style early assignment risk**
- SPY, QQQ, IWM are American options
- Deep ITM options can be assigned early (especially near ex-dividend)
- Currently modeled as European (hold until expiry or profit target)
- For far OTM options this is negligible — for near ATM on dividend dates it matters

---

## Priority Matrix

### High impact, low effort — do first
| # | Enhancement | Effort |
|---|-------------|--------|
| 1 | 2x stop loss scenario | 2 hours |
| 2 | Profit factor + avg win/loss + EV | 1 hour |
| 3 | CAGR (annualized return) | 30 min |
| 4 | Max consecutive losses | 30 min |
| 5 | Max dollar drawdown | 30 min |
| 6 | Sharpe with risk-free rate | 30 min |
| 7 | Premium capture % | 30 min |
| 8 | Theta + vega at entry in trade log | 1 hour |
| 9 | Fix expiry close intrinsic value | 2-3 hours |
| 10 | Buy and hold SPY benchmark | 1 hour |

### High impact, medium effort — do second
| # | Enhancement | Effort |
|---|-------------|--------|
| 11 | Short strangle strategy | 4-6 hours |
| 12 | Symbol-relative IV thresholds | 3-4 hours |
| 13 | VIX regime filter | 3 hours |
| 14 | Wheel % equity curve | 2 hours |
| 15 | Calmar ratio | 1 hour |
| 16 | Capital required + return on capital | 2 hours |
| 17 | IV expansion tracking during trade | 3-4 hours |
| 18 | roll20 in grid | 30 min |
| 19 | exit50_or_21dte scenario | 1 hour |

### Medium impact, high effort — do later
| # | Enhancement | Effort |
|---|-------------|--------|
| 20 | Iron condor strategy | 6-8 hours |
| 21 | Bull put spread strategy | 4-5 hours |
| 22 | PMCC strategy | 8-10 hours |
| 23 | Kelly criterion + position sizing | 4-5 hours |
| 24 | IV rank / IV percentile at entry | 3-4 hours |
| 25 | Realized vs implied vol spread | 3-4 hours |
| 26 | Roll breach scenario | 4-5 hours |
| 27 | Trend regime (200 SMA) | 2 hours |
| 28 | Dividend / early assignment flagging | 3 hours |
| 29 | Commission + slippage model | 2 hours |
| 30 | Short straddle strategy | 3-4 hours |
