# Development Roadmap

---

## Overview

Three phases. Each phase builds on the previous one.
Phase 1 makes the tool credible. Phase 2 makes it complete. Phase 3 makes it a business.

```
  Phase 1 — Foundation     Quant fixes + core stats + stop loss        4-6 weeks
  Phase 2 — Expansion      New strategies + greeks + regime depth       6-8 weeks
  Phase 3 — Monetization   Paywall + pricing + growth features          4 weeks
```

---

## Phase 1 — Foundation (do now)

Goal: fix every number that is currently wrong or misleading. Make the tool
defensible to a quant-savvy user. Addy can publish research using these numbers
with confidence.

---

### 1.1 Stop Loss Scenario

**What:** Add `exit_2x` scenario — close if option value reaches 2× the premium collected.

**Why:** Biggest gap in the tool. Every catastrophic loss in the research came from
having no stop. This is the #1 feature options traders ask for.

**Backend changes:**
- `simulate.py` — add `c_stop2x` check in daily loop (same pattern as c25/c50)
- Add `trades_stop2x` list and `build_curve` call
- Add `s_stop2x = mkstats(trades_stop2x, ...)` to stats
- Return in `curves`, `trade_log`, `stats`, `risk_table`

**Frontend changes:**
- Add "2× Stop" line to equity curve chart
- Add column to risk table
- Add tab in scenario switcher

**Effort:** 3 hours backend, 1 hour frontend

---

### 1.2 Core Stats Fixes

**What:** Fix Sharpe, add profit factor, avg win/loss, EV, CAGR, max dollar drawdown,
max consecutive losses, premium capture %.

**Why:** Current stats are incomplete. A user who knows options will immediately
notice the missing numbers and lose trust in the tool.

**Backend changes (all in `mkstats()` in simulate.py):**

```python
# Sharpe with risk-free rate
risk_free = 0.05
monthly_rf = risk_free / 12
sharpe = (mean(dr) - monthly_rf) / std(dr) * sqrt(12)

# CAGR
years = (end - start).days / 365.25
cagr = (curve[-1] / 100) ** (1 / years) - 1

# Profit factor
gross_wins  = sum(t['pnl'] for t in trades if t['pnl'] > 0)
gross_losses = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
profit_factor = gross_wins / gross_losses if gross_losses > 0 else None

# Avg win / avg loss
winners = [t['pnl'] for t in trades if t['pnl'] > 0]
losers  = [t['pnl'] for t in trades if t['pnl'] < 0]
avg_win  = mean(winners) if winners else None
avg_loss = mean(losers)  if losers  else None

# Expected value
ev = (win_rate/100 * avg_win) + ((1 - win_rate/100) * abs(avg_loss))

# Max consecutive losses
max_consec_loss = max streak of pnl < 0

# Max dollar drawdown
peak = 0; max_dd_dollar = 0
for t in trades:
    cum += t['pnl']
    peak = max(peak, cum)
    max_dd_dollar = max(max_dd_dollar, peak - cum)

# Premium capture %
premium_capture = (total_pnl / sum(t['premium_usd'] for t in trades)) * 100

# Calmar
calmar = cagr / abs(max_drawdown / 100) if max_drawdown != 0 else None
```

**Frontend changes:**
- Add new stat tiles to Summary tab: CAGR, Profit Factor, Avg Win, Avg Loss, EV, Premium Capture
- Update risk table with new columns

**Effort:** 2 hours backend, 2 hours frontend

---

### 1.3 Greeks at Entry in Trade Log

**What:** Add theta, vega, gamma from DB to each trade's entry row.

**Why:** Users want to know decay rate and IV sensitivity at entry. Theta capture
rate is one of the most useful stats for premium sellers.

**Backend changes:**
- `_find_entry_options()` SQL — add `f.theta, f.vega, f.gamma` to SELECT
- Pass through to trade log: `theta_at_entry`, `vega_at_entry`, `gamma_at_entry`
- Add `avg_theta_at_entry` and `theta_capture_rate` to mkstats()

**Frontend changes:**
- Add theta, vega to trade log table columns (hidden by default, toggle to show)

**Effort:** 2 hours backend, 1 hour frontend

---

### 1.4 Fix Expiry Close — Intrinsic Value (Fix A)

**What:** On expiry day, use intrinsic value instead of DB mark.
- ITM: close_price = |underlying_price - strike|
- OTM: close_price = 0

**Why:** DB marks on expiry day are stale or zero. This affects exitExp P&L and
CSP assignment cost basis accuracy.

**Backend changes:**
- In the daily loop, detect `td == exp_date`
- Use `underlying_prices.get(exp_date)` to compute intrinsic value
- Apply same fix to `_simulate_option_close()` for wheel

**Effort:** 2 hours

---

### 1.5 Buy and Hold SPY Benchmark

**What:** Fetch SPY buy-and-hold return for the same date range and include it
in the response.

**Why:** Users need context. "+62% strategy return" is meaningless without knowing
SPY returned +47% over the same period.

**Backend changes:**
- `simulate.py` — already fetches underlying prices via yfinance
- Compute: `spy_bh_return = (spy_prices[end] / spy_prices[start] - 1) * 100`
- Add `spy_benchmark` to response

**Frontend changes:**
- Add SPY line to equity curve chart (dashed gray line)
- Show "vs SPY +X%" next to total return stat tile

**Effort:** 1 hour backend, 1 hour frontend

---

### 1.6 roll20 in Grid

**What:** Add roll20 back to grid scenario comparisons.

**Why:** One line change. Users should see all scenarios in the grid.

**Backend changes:**
- `simulate.py run_grid()` — add `'roll20'` back to `grid_scenarios` list

**Effort:** 15 minutes

---

### 1.7 exit50 or 21 DTE Scenario

**What:** Close at 50% profit OR when 21 DTE remains, whichever comes first.

**Why:** Standard TastyTrade rule. Most popular exit rule in the options community.
Different from pure exit50 because it cuts off time risk near expiry.

**Backend changes:**
- Add `c_50_or_21` check in daily loop:
  ```python
  if c_50_or_21 is None and (mark <= t50 or dte_remaining <= 21):
      c_50_or_21 = {...}
  ```
- Add `trades_50_or_21`, stats, curve
- Add to response and risk table

**Effort:** 2 hours backend, 1 hour frontend

---

### Phase 1 Deliverables Summary

| # | Feature | Backend | Frontend | Total |
|---|---------|---------|----------|-------|
| 1.1 | Stop loss 2x scenario | 3h | 1h | 4h |
| 1.2 | Core stats fixes | 2h | 2h | 4h |
| 1.3 | Greeks at entry | 2h | 1h | 3h |
| 1.4 | Fix expiry intrinsic value | 2h | 0h | 2h |
| 1.5 | SPY benchmark | 1h | 1h | 2h |
| 1.6 | roll20 in grid | 0.25h | 0h | 0.25h |
| 1.7 | exit50 or 21 DTE | 2h | 1h | 3h |
| **Total** | | **12.25h** | **6h** | **~18h** |

---

## Phase 2 — Expansion (do after Phase 1)

Goal: new strategies, deeper regime analysis, richer data. By end of Phase 2
the tool covers everything a serious options trader needs for research.

---

### 2.1 Short Strangle Strategy

**What:** Sell OTM call + OTM put simultaneously. Manage as one combined position.

**Why:** The research article Addy just published is about this exact strategy.
The tool should be able to reproduce it. Highest-demand new strategy.

**Backend changes:**
- New `run_strangle_simulation()` function
- Entry: find both call (17 delta) and put (17 delta) for same expiry
- Combined mark = call_mark + put_mark
- Exit: combined_mark <= 50% of combined_premium_collected (or 2x stop)
- Stats: call_breach_rate, put_breach_rate, which_leg_tested
- New `/strangle` endpoint in main.py

**Frontend changes:**
- Add "Short Strangle" to strategy selector
- New result view: show call leg + put leg + combined P&L
- Breach rate visualization (call side vs put side)

**Effort:** 6 hours backend, 4 hours frontend

---

### 2.2 Iron Condor Strategy

**What:** Short strangle + long wings. Sell 17-delta call/put, buy 5-delta call/put.

**Why:** Defined max loss. Removes catastrophic risk of naked strangle.
Most popular strategy for risk-conscious premium sellers.

**New concepts:**
- wing_width (distance between short and long strikes)
- net_credit (strangle premium - wing cost)
- max_loss_per_trade = (wing_width - net_credit) × 100
- risk_reward_ratio = net_credit / max_loss_per_trade

**Backend changes:**
- Extend `run_strangle_simulation()` with optional `use_wings=True`
- Find 5-delta call and put for same expiry (already know how to query)
- Deduct wing cost from net credit
- New stats: max_loss, risk_reward, defined_risk_return

**Effort:** 5 hours backend, 3 hours frontend

---

### 2.3 Bull Put Spread Strategy

**What:** Sell OTM put (17-30 delta), buy further OTM put (5-10 delta) as protection.

**Why:** Defined-risk version of CSP. Lower premium but capped loss.
Better for beginners who want to learn before going naked.

**Backend changes:**
- Query: find short put (17 delta) + long put (5 delta) same expiry
- Net credit = short_put_mark - long_put_mark
- Max loss = (short_strike - long_strike - net_credit) × 100

**Effort:** 4 hours backend, 2 hours frontend

---

### 2.4 VIX Regime Filter

**What:** Add VIX-based entry filter alongside IV regime. Low (<15), Normal (15-25), High (>25).

**Why:** VIX is a cleaner market-wide fear signal than single-option IV.
Shows strategy performance across different market fear regimes.

**Backend changes:**
- Fetch VIX daily from yfinance (free, already using yfinance)
- Cache in memory during simulation run
- Add `vix_at_entry` to trade log
- Add `vix_regime` filter parameter (same pattern as `iv_regime`)

**Frontend changes:**
- Add VIX regime toggle alongside IV regime toggle

**Effort:** 3 hours backend, 1 hour frontend

---

### 2.5 Trend Regime Filter

**What:** Filter entries by whether SPY is above or below its 200-day SMA.

**Why:** CC performs worse in downtrends (short calls get tested more).
CSP performs worse in downtrends (puts get tested). Shows regime sensitivity.

**Backend changes:**
- Compute 200-day SMA from already-fetched underlying prices
- Add `trend_regime` param: 'all', 'uptrend', 'downtrend'
- Add `spy_vs_200sma` to trade log

**Effort:** 2 hours backend, 1 hour frontend

---

### 2.6 Symbol-Relative IV Thresholds

**What:** Compute per-symbol IV percentile thresholds from DB instead of fixed 18%.

**Why:** 18% is SPY-specific. For QQQ/IWM the baseline is different.
Fixes the IV regime filter for non-SPY symbols.

**Backend changes:**
- Query: for each symbol, compute 33rd and 67th percentile of historical IV
- Cache per symbol
- Replace hardcoded 0.18 with symbol-specific thresholds
- Expose thresholds in API response so frontend can display them

**Effort:** 3 hours

---

### 2.7 IV Expansion Tracking During Trade

**What:** For each trade, track max IV reached and IV expansion from entry.

**Why:** Quantifies the Feb 2020 type risk. Shows which trades had IV spike
mid-life even if they ultimately closed profitably.

**Backend changes:**
- In daily loop, track `max_iv_during_trade`
- Compute `iv_expansion = max_iv_during_trade - iv_at_entry`
- Add to trade log
- Add aggregate stat: `avg_iv_expansion`, `max_iv_expansion`

**Effort:** 3 hours

---

### 2.8 Position Sizing Output

**What:** Given a user-input account size, show how many contracts to trade
and what % of capital is at risk.

**Why:** The #1 question after "does this strategy work" is "how much should I trade".

**Backend changes:**
- Add optional `account_size` param to SimulateRequest
- Compute: `contracts = floor(account_size × kelly_fraction / capital_required)`
- Add `sizing` block to response

**Frontend changes:**
- Add account size input (optional)
- Show sizing recommendation in stats

**Effort:** 3 hours backend, 2 hours frontend

---

### 2.9 Realized vs Implied Vol Spread

**What:** Compute HV30 (30-day historical realized vol) and compare to IV at entry.

**Why:** IV - HV = premium edge. High spread = options are expensive relative
to actual moves. Shows how much edge the strategy had at each entry.

**Backend changes:**
- Compute HV30 from daily underlying prices (already fetched)
- HV30 = annualized std dev of 30 daily log returns
- Add `hv30_at_entry`, `iv_hv_spread` to trade log
- Add avg_iv_hv_spread to stats

**Effort:** 3 hours

---

### 2.10 Wheel Deep Fixes

**What:** Fix expiry intrinsic (already in 1.4), add % curve, round duration stats,
adjust CALL delta for cost basis distance.

**Already covered in 1.4**

**Additional:**
- Wheel % equity curve (use first PUT strike × 100 as capital base)
- Round stats: avg_round_duration, longest_round, num_complete_rounds
- CALL delta adjustment: if (spot - cost_basis) / spot > 0.10, raise delta to 0.25

**Effort:** 4 hours backend, 2 hours frontend

---

### Phase 2 Deliverables Summary

| # | Feature | Backend | Frontend | Total |
|---|---------|---------|----------|-------|
| 2.1 | Short strangle | 6h | 4h | 10h |
| 2.2 | Iron condor | 5h | 3h | 8h |
| 2.3 | Bull put spread | 4h | 2h | 6h |
| 2.4 | VIX regime filter | 3h | 1h | 4h |
| 2.5 | Trend regime filter | 2h | 1h | 3h |
| 2.6 | Symbol-relative IV thresholds | 3h | 0h | 3h |
| 2.7 | IV expansion tracking | 3h | 1h | 4h |
| 2.8 | Position sizing output | 3h | 2h | 5h |
| 2.9 | Realized vs implied vol | 3h | 1h | 4h |
| 2.10 | Wheel deep fixes | 4h | 2h | 6h |
| **Total** | | **36h** | **17h** | **~53h** |

---

## Phase 3 — Monetization (do after Phase 2)

Covered in ARCHITECTURE.md. High level:

```
  Stripe integration          payments + subscription management
  Clerk role gating           free vs paid user roles
  Feature gates               date range limits, symbol limits, strategy limits
  Pricing page                landing page with tier comparison
  Usage tracking              PostHog events for conversion funnel
```

**Effort:** ~20-25 hours total

---

## Data Backfill Needed to Support Phase 2

Current data coverage:

| Symbol | Coverage | Status |
|--------|----------|--------|
| SPY | 2023–2026 (full), 2021–2022 (full), 2020 (full), 2019 (full), 2018 (partial), 2015–2017 (partial), 2006–2014 (partial via pre-2018 plan) | Good |
| QQQ | Partial | Needs backfill |
| IWM | Partial | Needs backfill |
| AAPL | 2016–2018 running | In progress |
| BITO | 2022–2026 running | In progress |
| IBIT | 2024–2026 running | In progress |

To support full strangle/condor/spread simulations on QQQ and IWM:
- Need at least 5 years of data per symbol
- QQQ: backfill 2020–2024 minimum
- IWM: backfill 2020–2024 minimum

---

## Technical Debt to Address

**Before Phase 2:**
- `make_docx.py` contains DB password — never commit (already blocked by GitHub)
- `.vscode/` folder committed to repo — remove
- `api/chain_*.py` files on droplet not in git — document or add to gitignore
- Droplet git pull now works (repo public) — remove SFTP deploy workaround from memory

**Performance:**
- Grid runs 12 simulations in parallel (ThreadPoolExecutor, max_workers=9)
- Each simulation hits DB with 2-3 queries
- Under concurrent users: DB connections could saturate
- Fix before monetizing: add connection pooling (SQLAlchemy pool_size=10)

**Monitoring:**
- No uptime monitoring on droplet API
- Add: UptimeRobot free tier (pings every 5 min, Slack alert if down)
- No error tracking — add Sentry free tier to FastAPI

---

## Timeline

```
  Week 1-2    Phase 1.1 → 1.4 (stop loss, stats fixes, greeks, expiry fix)
  Week 3      Phase 1.5 → 1.7 (benchmark, roll20, exit50+21dte)
  Week 4      Testing, QA, deploy Phase 1
  
  Week 5-6    Phase 2.1 → 2.3 (short strangle, iron condor, bull put spread)
  Week 7-8    Phase 2.4 → 2.7 (VIX, trend, IV thresholds, IV expansion)
  Week 9-10   Phase 2.8 → 2.10 (sizing, vol spread, wheel fixes)
  Week 11     Data backfills (QQQ, IWM 5yr)
  Week 12     Testing, QA, deploy Phase 2
  
  Week 13-16  Phase 3 (monetization) — see ARCHITECTURE.md
```
