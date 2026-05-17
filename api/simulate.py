"""
Covered call backtest engine — optimized query version.
Step 1: Find entry option for each monthly date (12-24 small queries).
Step 2: Load all daily prices for selected options in ONE bulk query.
All P&L computation in Python — no per-row DB round trips.

Upgrades v2:
- Mid price entry (bid+ask)/2 instead of ask
- ATM IV filter (min_iv param — skip low-IV months)
- Roll at 20 DTE scenario (exit when DTE <= 20)
- 3x3 delta x DTE grid (run_grid)
- Yearly P&L breakdown in stats
"""
from datetime import date, timedelta
from typing import List, Optional
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import pandas as pd
import yfinance as yf
from .db import query


def _fetch_underlying_prices(symbol: str, start: date, end: date) -> dict:
    """Fetch daily closing prices for the underlying via yfinance.
    Returns {date: close_price} dict.
    """
    try:
        df = yf.Ticker(symbol).history(start=str(start), end=str(end + timedelta(days=5)))
        if df.empty:
            return {}
        return {d.date(): round(float(v), 4) for d, v in df['Close'].items()}
    except Exception:
        return {}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _monthly_entry_dates(start: date, end: date, target_dte: int) -> List[tuple]:
    """Return (entry_target_date, expiry_date) pairs for each month."""
    pairs = []
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        first = date(year, month, 1)
        first_friday = first + timedelta(days=(4 - first.weekday()) % 7)
        third_friday = first_friday + timedelta(weeks=2)
        if start <= third_friday <= end:
            entry_target = third_friday - timedelta(days=target_dte)
            # Pre-Feb 2015 SPY options expired Saturday (day after 3rd Friday)
            expiry = third_friday + timedelta(days=1) if third_friday < date(2015, 2, 1) else third_friday
            pairs.append((entry_target, expiry))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return pairs


def _get_underlying_id(symbol: str) -> Optional[int]:
    rows = query("SELECT underlying_id FROM dim_underlying WHERE symbol = :s", {'s': symbol})
    return int(rows[0]['underlying_id']) if rows else None


def _find_entry_options(symbol: str, pairs: List[tuple],
                        target_delta: float, target_dte: int,
                        call_put: str = 'C') -> pd.DataFrame:
    """
    Find the best option for each (entry_date, expiry_date) pair.
    call_put='C' for covered call, 'P' for cash-secured put.
    For puts, target_delta is the absolute value (e.g. 0.25 → filters delta BETWEEN -0.33 AND -0.17).
    """
    if not pairs:
        return pd.DataFrame()

    uid = _get_underlying_id(symbol)
    if uid is None:
        return pd.DataFrame()

    entry_dates   = "', '".join(str(p[0]) for p in pairs)
    expiry_dates  = "', '".join(str(p[1]) for p in pairs)

    if call_put == 'P':
        # Put deltas are negative in DB; filter by absolute value band
        delta_low  = round(-(target_delta + 0.08), 3)
        delta_high = round(-(target_delta - 0.08), 3)
        delta_sign_filter = "AND f.delta < 0"
    else:
        delta_low  = round(target_delta - 0.08, 3)
        delta_high = round(target_delta + 0.08, 3)
        delta_sign_filter = "AND f.delta > 0"

    sql = f"""
        SELECT
            f.option_id,
            f.trade_date,
            f.bid,
            f.ask,
            f.iv,
            f.delta,
            o.expiration_date,
            o.strike,
            DATEDIFF(o.expiration_date, f.trade_date) AS dte
        FROM fact_option_eod f
        JOIN dim_option_contract o USING(option_id)
        WHERE o.underlying_id   = {uid}
          AND f.trade_date      IN ('{entry_dates}')
          AND o.expiration_date IN ('{expiry_dates}')
          AND o.call_put         = '{call_put}'
          AND f.delta           BETWEEN :delta_low AND :delta_high
          AND f.ask              > 0
          {delta_sign_filter}
    """
    rows = query(sql, {'delta_low': delta_low, 'delta_high': delta_high})
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    df['trade_date']      = pd.to_datetime(df['trade_date']).dt.date
    df['expiration_date'] = pd.to_datetime(df['expiration_date']).dt.date
    for col in ['bid', 'ask', 'delta', 'strike', 'iv']:
        df[col] = df[col].astype(float)
    df['dte']  = df['dte'].astype(int)
    df['mark'] = (df['bid'] + df['ask']) / 2
    # Score by abs(delta) distance from target
    df['score'] = (abs(abs(df['delta']) - target_delta) +
                   abs(df['dte'] - target_dte) * 0.01)

    return df.sort_values(['score', 'option_id']).groupby('trade_date', as_index=False).first()


def _find_next_monthly_option(symbol: str, roll_date: date, target_strike: float,
                               target_delta: float, call_put: str = 'C') -> Optional[dict]:
    """
    Find the next monthly expiry option closest to target_strike after roll_date.
    Falls back to closest delta if exact strike not available.
    Returns dict with option_id, strike, expiration_date, mark, delta, iv or None.
    """
    uid = _get_underlying_id(symbol)
    if uid is None:
        return None

    # Look for next monthly expiry (third Friday) within 30-75 days
    exp_from = roll_date + timedelta(days=15)
    exp_to   = roll_date + timedelta(days=75)

    sql = f"""
        SELECT
            f.option_id, f.bid, f.ask, f.delta, f.iv,
            o.expiration_date, o.strike,
            DATEDIFF(o.expiration_date, f.trade_date) AS dte,
            ABS(o.strike - :target_strike) AS strike_diff
        FROM fact_option_eod f
        JOIN dim_option_contract o USING(option_id)
        WHERE o.underlying_id   = {uid}
          AND f.trade_date      = :roll_date
          AND o.expiration_date BETWEEN :exp_from AND :exp_to
          AND o.call_put        = '{call_put}'
          AND f.ask             > 0
        ORDER BY strike_diff, ABS(ABS(f.delta) - :target_delta)
        LIMIT 1
    """
    rows = query(sql, {
        'roll_date':     str(roll_date),
        'exp_from':      str(exp_from),
        'exp_to':        str(exp_to),
        'target_strike': target_strike,
        'target_delta':  target_delta,
    })
    if not rows:
        return None
    r = dict(rows[0])
    r['mark'] = (float(r['bid']) + float(r['ask'])) / 2
    r['expiration_date'] = r['expiration_date'] if isinstance(r['expiration_date'], date) else date.fromisoformat(str(r['expiration_date']))
    return r


def _load_price_history(option_ids: List[int], start: date, end: date) -> pd.DataFrame:
    """
    ONE query: load all daily prices for the selected option IDs.
    Returns DataFrame indexed by (option_id, trade_date).
    """
    if not option_ids:
        return pd.DataFrame()

    id_list = ','.join(str(i) for i in option_ids)
    sql = f"""
        SELECT option_id, trade_date, bid, ask, price
        FROM fact_option_eod
        WHERE option_id IN ({id_list})
          AND trade_date BETWEEN :start AND :end
        ORDER BY option_id, trade_date
    """
    rows = query(sql, {'start': start, 'end': end})
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
    for col in ['bid', 'ask', 'price']:
        df[col] = df[col].astype(float)
    df['mark'] = (df['bid'] + df['ask']) / 2
    df.loc[df['ask'] == 0, 'mark'] = df.loc[df['ask'] == 0, 'price']
    return df.set_index(['option_id', 'trade_date'])


def _sharpe(returns: list, risk_free: float = 0.05) -> float:
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0
    if std == 0:
        return 0.0
    return round((mean - risk_free / 12) / std * math.sqrt(12), 3)


def _max_drawdown(curve: list) -> float:
    if not curve:
        return 0.0
    peak, max_dd = curve[0], 0.0
    for v in curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd * 100, 2)


def _yearly_pnl(trades: list) -> dict:
    """Group total P&L by year based on open_date."""
    by_year = defaultdict(float)
    for t in trades:
        year = t['open_date'][:4]
        by_year[year] += t['pnl']
    return {yr: round(pnl, 2) for yr, pnl in sorted(by_year.items())}


# ── Main simulation ───────────────────────────────────────────────────────────

def run_simulation(symbol: str, target_delta: float, target_dte: int,
                   start: date, end: date, min_iv: Optional[float] = None,
                   strategy: str = 'cc', iv_regime: str = 'all'):
    """
    Run options strategy backtest.

    Args:
        symbol:       Ticker (e.g. 'SPY')
        target_delta: Target delta absolute value (e.g. 0.25)
        target_dte:   Target days to expiry at entry (e.g. 30)
        start:        Backtest start date
        end:          Backtest end date
        min_iv:       Minimum IV to enter trade (skip low-IV months)
        strategy:     'cc' = covered call (default), 'csp' = cash-secured put
        iv_regime:    'all' = no filter, 'high' = IV >= 18%, 'low' = IV < 18%
    """
    pairs = _monthly_entry_dates(start, end, target_dte)
    if not pairs:
        return None

    candidate_pairs = []
    entry_map = {}
    for entry_target, exp_date in pairs:
        for offset in [0, -1, 1, -2, 2, -3, 3]:
            d = entry_target + timedelta(days=offset)
            if start <= d < exp_date:
                candidate_pairs.append((d, exp_date))
                entry_map[d] = exp_date
                break

    if not candidate_pairs:
        return None

    call_put = 'P' if strategy == 'csp' else 'C'
    entries_df = _find_entry_options(symbol, candidate_pairs, target_delta, target_dte, call_put=call_put)
    if entries_df.empty:
        return None

    # Apply ATM IV filter — skip months where entry option IV < min_iv
    if min_iv is not None and min_iv > 0 and 'iv' in entries_df.columns:
        before = len(entries_df)
        entries_df = entries_df[entries_df['iv'] >= min_iv]
        if entries_df.empty:
            return None

    # Apply IV regime filter
    if iv_regime == 'high':
        entries_df = entries_df[entries_df['iv'] >= 0.18]
    elif iv_regime == 'low':
        entries_df = entries_df[entries_df['iv'] < 0.18]
    if entries_df.empty:
        return None

    option_ids = entries_df['option_id'].tolist()
    eod = _load_price_history(option_ids, start, end)

    # Fetch underlying prices once for share P&L calculation
    underlying_prices = _fetch_underlying_prices(symbol, start, end)

    _ROLL15_ENABLED = False  # disabled until curve inflation bug is fixed
    trades_25, trades_50, trades_exp, trades_roll20, trades_roll15 = [], [], [], [], []

    for _, row in entries_df.iterrows():
        oid        = int(row['option_id'])
        entry_date = row['trade_date']
        exp_date   = entry_map.get(entry_date, row['expiration_date'])

        # Mid price entry (upgrade: was ask price)
        premium = float(row['mark'])

        base = {
            'open_date':   str(entry_date),
            'expiry':      str(exp_date),
            'strike':      float(row['strike']),
            'delta':       round(float(row['delta']), 3),
            'dte_at_open': int(row['dte']),
            'iv_at_open':  round(float(row['iv']), 4) if row['iv'] > 0 else None,
            'premium':     round(premium, 4),
            'premium_usd': round(premium * 100, 2),
        }

        t25 = premium * 0.25
        t50 = premium * 0.50
        c25 = c50 = c_roll20 = None

        if oid in eod.index.get_level_values(0):
            daily = eod.loc[oid]
            daily = daily[(daily.index > entry_date) & (daily.index <= exp_date)]
        else:
            daily = pd.DataFrame()

        for td, pr in daily.iterrows():
            mark = float(pr['mark'])
            dte_remaining = (exp_date - td).days

            if c25 is None and mark <= t25:
                c25 = {'close_date': str(td), 'close_price': round(mark, 4),
                       'pnl': round((premium - mark) * 100, 2),
                       'days_held': (td - entry_date).days}
            if c50 is None and mark <= t50:
                c50 = {'close_date': str(td), 'close_price': round(mark, 4),
                       'pnl': round((premium - mark) * 100, 2),
                       'days_held': (td - entry_date).days}
            # Roll at 20 DTE: close position when DTE drops to 20
            if c_roll20 is None and dte_remaining <= 20:
                c_roll20 = {'close_date': str(td), 'close_price': round(mark, 4),
                            'pnl': round((premium - mark) * 100, 2),
                            'days_held': (td - entry_date).days}

            if c25 and c50 and c_roll20:
                break

        exp_mark = 0.0
        if not daily.empty and exp_date in daily.index:
            exp_mark = max(float(daily.loc[exp_date, 'mark']), 0.0)

        cexp = {'close_date': str(exp_date), 'close_price': round(exp_mark, 4),
                'pnl': round((premium - exp_mark) * 100, 2),
                'days_held': (exp_date - entry_date).days}

        # Fix #2: CSP assignment tracking
        is_csp = strategy == 'csp'
        spy_at_entry = underlying_prices.get(entry_date)
        underlying_at_exp = underlying_prices.get(exp_date)
        assigned_at_exp = (
            is_csp
            and underlying_at_exp is not None
            and underlying_at_exp < float(row['strike'])
        )
        cost_basis = round(float(row['strike']) - premium, 4) if assigned_at_exp else None
        cost_basis_discount = (
            round(spy_at_entry - cost_basis, 4)
            if assigned_at_exp and spy_at_entry is not None and cost_basis is not None
            else None
        )

        # Share P&L: underlying price change × 100 shares (CC only; CSP holds cash not stock)
        spy_open = spy_at_entry if strategy == 'cc' else None

        def _with_pnl(close_dict: dict, is_expiry_close: bool = False) -> dict:
            opt_pnl = close_dict.get('pnl', 0)

            assignment_fields = {
                'assigned':            assigned_at_exp if is_expiry_close else False,
                'cost_basis':          cost_basis if is_expiry_close else None,
                'cost_basis_discount': cost_basis_discount if is_expiry_close else None,
            } if is_csp else {'assigned': None, 'cost_basis': None, 'cost_basis_discount': None}

            if strategy == 'cc' and spy_open is not None:
                close_date_str = close_dict.get('close_date', str(exp_date))
                spy_close = underlying_prices.get(
                    date.fromisoformat(close_date_str),
                    underlying_prices.get(exp_date)
                )
                if spy_close is not None:
                    share_pnl = round((spy_close - spy_open) * 100, 2)
                    total_pnl = round(opt_pnl + share_pnl, 2)
                    return {**close_dict, 'option_pnl': opt_pnl, 'share_pnl': share_pnl,
                            'total_pnl': total_pnl, 'spy_entry': spy_open, 'spy_exit': spy_close,
                            **assignment_fields}
            return {**close_dict, 'option_pnl': opt_pnl, 'share_pnl': None,
                    'total_pnl': None, 'spy_entry': None, 'spy_exit': None,
                    **assignment_fields}

        trades_25.append({**base,    **_with_pnl(c25      or cexp, is_expiry_close=(c25      is None)), 'scenario': 'exit25'})
        trades_50.append({**base,    **_with_pnl(c50      or cexp, is_expiry_close=(c50      is None)), 'scenario': 'exit50'})
        trades_exp.append({**base,   **_with_pnl(cexp,             is_expiry_close=True),               'scenario': 'exitExp'})
        trades_roll20.append({**base,**_with_pnl(c_roll20 or cexp, is_expiry_close=(c_roll20 is None)), 'scenario': 'roll20'})

        if _ROLL15_ENABLED:
            # ── Roll at 15 DTE (max 2 rolls, same strike, hold final leg to expiry) ──
            roll_chain_pnl   = 0.0
            roll_chain_days  = 0
            roll_total_prem  = round(premium * 100, 2)
            cur_oid          = oid
            cur_entry        = entry_date
            cur_exp          = exp_date
            cur_premium      = premium
            cur_strike       = float(row['strike'])
            rolls_done       = 0
            MAX_ROLLS        = 2
            final_close_date = str(exp_date)
            final_close_px   = round(exp_mark, 4)

            for _roll_attempt in range(MAX_ROLLS + 1):  # initial leg + up to 2 rolls
                leg_eod_ids = [cur_oid]
                leg_eod = _load_price_history(leg_eod_ids, cur_entry, cur_exp + timedelta(days=5))

                leg_daily = pd.DataFrame()
                if cur_oid in leg_eod.index.get_level_values(0):
                    leg_daily = leg_eod.loc[cur_oid]
                    leg_daily = leg_daily[(leg_daily.index > cur_entry) & (leg_daily.index <= cur_exp)]

                t50_leg      = cur_premium * 0.50
                t25_leg      = cur_premium * 0.75
                roll_trigger = None
                roll_mark    = None
                early_close  = None

                for td, pr in leg_daily.iterrows():
                    mark    = float(pr['mark'])
                    dte_rem = (cur_exp - td).days
                    if rolls_done > 0 and early_close is None and mark <= t50_leg:
                        early_close = {'date': td, 'mark': mark}
                    if roll_trigger is None and dte_rem <= 15:
                        roll_trigger = td
                        roll_mark    = mark
                        break

                if early_close is not None:
                    leg_pnl = round((cur_premium - early_close['mark']) * 100, 2)
                    roll_chain_pnl  += leg_pnl
                    roll_chain_days += (early_close['date'] - cur_entry).days
                    final_close_date = str(early_close['date'])
                    final_close_px   = round(early_close['mark'], 4)
                    break

                if roll_trigger is None or rolls_done >= MAX_ROLLS:
                    leg_exp_mark = 0.0
                    if not leg_daily.empty and cur_exp in leg_daily.index:
                        leg_exp_mark = max(float(leg_daily.loc[cur_exp, 'mark']), 0.0)
                    leg_pnl = round((cur_premium - leg_exp_mark) * 100, 2)
                    roll_chain_pnl  += leg_pnl
                    roll_chain_days += (cur_exp - cur_entry).days
                    final_close_date = str(cur_exp)
                    final_close_px   = round(leg_exp_mark, 4)
                    break

                profit_achieved = roll_mark <= t25_leg
                if profit_achieved:
                    leg_pnl = round((cur_premium - roll_mark) * 100, 2)
                    roll_chain_pnl  += leg_pnl
                    roll_chain_days += (roll_trigger - cur_entry).days
                    final_close_date = str(roll_trigger)
                    final_close_px   = round(roll_mark, 4)
                    break

                leg_pnl = round((cur_premium - roll_mark) * 100, 2)
                roll_chain_pnl  += leg_pnl
                roll_chain_days += (roll_trigger - cur_entry).days

                next_opt = _find_next_monthly_option(
                    symbol, roll_trigger, cur_strike, target_delta, call_put
                )
                if next_opt is None:
                    leg_exp_mark = 0.0
                    if not leg_daily.empty and cur_exp in leg_daily.index:
                        leg_exp_mark = max(float(leg_daily.loc[cur_exp, 'mark']), 0.0)
                    roll_chain_pnl  += round((cur_premium - leg_exp_mark) * 100, 2)
                    roll_chain_days += (cur_exp - roll_trigger).days
                    final_close_date = str(cur_exp)
                    final_close_px   = round(leg_exp_mark, 4)
                    break

                rolls_done   += 1
                new_premium   = float(next_opt['mark'])
                roll_total_prem += round(new_premium * 100, 2)
                cur_oid       = int(next_opt['option_id'])
                cur_entry     = roll_trigger
                cur_exp       = next_opt['expiration_date']
                cur_premium   = new_premium
                final_close_date = str(cur_exp)

            trades_roll15.append({
                **base,
                'close_date':  final_close_date,
                'close_price': final_close_px,
                'pnl':         round(roll_chain_pnl, 2),
                'days_held':   roll_chain_days,
                'rolls_done':  rolls_done,
                'option_pnl':  round(roll_chain_pnl, 2),
                'share_pnl':   None,
                'total_pnl':   None,
                'spy_entry':   None,
                'spy_exit':    None,
                'scenario':    'roll15',
            })

    if not trades_25:
        return None

    # Build equity curves
    def build_curve(trades):
        s = sorted(trades, key=lambda t: t['open_date'])
        dates, vals, cum = [s[0]['open_date']], [100.0], 100.0
        for t in s:
            denom = (t['strike'] * 100) if strategy == 'csp' else (t.get('spy_entry') or 100) * 100
            cum = cum * (1 + t['pnl'] / denom)
            dates.append(t['close_date'])
            vals.append(round(cum, 2))
        return dates, vals

    d25,  c25v  = build_curve(trades_25)
    d50,  c50v  = build_curve(trades_50)
    dex,  cexv  = build_curve(trades_exp)
    dr20, cr20v = build_curve(trades_roll20)
    dr15, cr15v = build_curve(trades_roll15) if trades_roll15 else ([], [100.0])
    all_dates   = sorted(set(d25 + d50 + dex + dr20 + dr15))

    def interp(dates, vals):
        out, last, i = [], 100.0, 0
        for d in all_dates:
            while i < len(dates) and dates[i] <= d:
                last = vals[i]; i += 1
            out.append(last)
        return out

    def mkstats(trades, curve):
        n = len(trades)
        dr = [(curve[i] - curve[i-1]) / curve[i-1] for i in range(1, len(curve))]
        share_pnls = [t['share_pnl'] for t in trades if t.get('share_pnl') is not None]
        opt_pnls   = [t['option_pnl'] for t in trades if t.get('option_pnl') is not None]
        total_pnls = [t['total_pnl']  for t in trades if t.get('total_pnl')  is not None]

        assigned_trades = [t for t in trades if t.get('assigned') is True]
        cb_discounts    = [t['cost_basis_discount'] for t in trades if t.get('cost_basis_discount') is not None]

        stats = {
            'num_trades':       n,
            'total_pnl':        round(sum(t['pnl'] for t in trades), 2),
            'total_option_pnl': round(sum(opt_pnls), 2)   if opt_pnls   else None,
            'total_share_pnl':  round(sum(share_pnls), 2) if share_pnls else None,
            'total_combined_pnl': round(sum(total_pnls), 2) if total_pnls else None,
            'avg_premium':      round(sum(t['premium_usd'] for t in trades) / n, 2),
            'avg_pnl':          round(sum(t['pnl'] for t in trades) / n, 2),
            'avg_days_held':    round(sum(t['days_held'] for t in trades) / n, 1),
            'win_rate':         round(sum(1 for t in trades if t['pnl'] > 0) / n * 100, 1),
            'total_return':     round(curve[-1] - 100, 2),
            'max_drawdown':     _max_drawdown(curve),
            'sharpe':           _sharpe(dr),
            'yearly_pnl':       _yearly_pnl(trades),
        }

        if strategy == 'csp':
            stats['assignment_rate']         = round(len(assigned_trades) / n * 100, 1)
            stats['num_assigned']            = len(assigned_trades)
            stats['avg_cost_basis_discount'] = round(sum(cb_discounts) / len(cb_discounts), 2) if cb_discounts else None

        return stats

    s25  = mkstats(trades_25,     c25v)
    s50  = mkstats(trades_50,     c50v)
    sex  = mkstats(trades_exp,    cexv)
    sr20 = mkstats(trades_roll20, cr20v)
    sr15 = mkstats(trades_roll15, cr15v) if trades_roll15 else {}

    def rrow(label, key, s):
        return {'scenario': label, 'key': key, **{k: s.get(k, 0)
                for k in ['total_return', 'max_drawdown', 'sharpe', 'num_trades',
                           'avg_premium', 'avg_pnl', 'avg_days_held', 'win_rate']}}

    return {
        'curves': {
            'dates':   all_dates,
            'exit25':  interp(d25,  c25v),
            'exit50':  interp(d50,  c50v),
            'exitExp': interp(dex,  cexv),
            'roll20':  interp(dr20, cr20v),
            'roll15':  interp(dr15, cr15v) if trades_roll15 else [],
        },
        'trade_log': {
            'exit25':  trades_25,
            'exit50':  trades_50,
            'exitExp': trades_exp,
            'roll20':  trades_roll20,
            'roll15':  trades_roll15,
        },
        'stats': {
            'exit25':  s25,
            'exit50':  s50,
            'exitExp': sex,
            'roll20':  sr20,
            'roll15':  sr15,
        },
        'risk_table': [
            rrow('Close at 25%',    'exit25',  s25),
            rrow('Close at 50%',    'exit50',  s50),
            rrow('Hold to Expiry',  'exitExp', sex),
            rrow('Roll at 20 DTE',  'roll20',  sr20),
            rrow('Roll at 15 DTE',  'roll15',  sr15),
        ],
    }


# ── 3x3 Grid ─────────────────────────────────────────────────────────────────

def run_grid(symbol: str, start: date, end: date,
             min_iv: Optional[float] = None, strategy: str = 'cc', iv_regime: str = 'all') -> dict:
    """
    Run all 9 delta x DTE combinations and return a grid of stats.

    Grid:
        delta:  0.20, 0.30, 0.40
        dte:    7,    14,   30

    Returns:
        {
          'grid': [
            { 'delta': 0.20, 'dte': 7,  'exit25': {...stats}, 'exitExp': {...}, ... },
            ...
          ],
          'best': { scenario: 'exit50', delta: 0.30, dte: 30, total_return: 48.2 }
        }
    """
    # roll20 disabled in grid — deferred until Addy gives go-ahead to re-enable
    # to re-enable: add 'roll20' back to grid_scenarios
    deltas = [0.17, 0.25, 0.35, 0.50]
    dtes   = [30, 45, 60]
    grid_scenarios = ['exit25', 'exit50', 'exitExp']
    combos = [(d, t) for d in deltas for t in dtes]
    best   = None

    def _run_combo(delta, dte):
        result = run_simulation(symbol, delta, dte, start, end, min_iv=min_iv, strategy=strategy, iv_regime=iv_regime)
        if result is None:
            return None
        cell = {'delta': delta, 'dte': dte}
        for scenario in grid_scenarios:
            s = result['stats'].get(scenario, {})
            if not s:
                continue
            cell[scenario] = {
                'total_return': s.get('total_return', 0),
                'total_pnl':    s.get('total_pnl', 0),
                'max_drawdown': s.get('max_drawdown', 0),
                'sharpe':       s.get('sharpe', 0),
                'win_rate':     s.get('win_rate', 0),
                'num_trades':   s.get('num_trades', 0),
                'avg_pnl':      s.get('avg_pnl', 0),
                'avg_premium':  s.get('avg_premium', 0),
                'yearly_pnl':   s.get('yearly_pnl', {}),
            }
        return cell

    raw_cells = []
    with ThreadPoolExecutor(max_workers=9) as pool:
        futures = {pool.submit(_run_combo, d, t): (d, t) for d, t in combos}
        for fut in as_completed(futures):
            cell = fut.result()
            if cell is not None:
                raw_cells.append(cell)

    # Sort to stable order: delta asc, dte asc
    grid = sorted(raw_cells, key=lambda c: (c['delta'], c['dte']))

    for cell in grid:
        for scenario in grid_scenarios:
            s = cell.get(scenario, {})
            tr = s.get('total_return', 0)
            if best is None or tr > best['total_return']:
                best = {
                    'scenario':     scenario,
                    'delta':        cell['delta'],
                    'dte':          cell['dte'],
                    'total_return': tr,
                    'sharpe':       s.get('sharpe', 0),
                }

    return {'grid': grid, 'best': best}


# ── Wheel strategy ────────────────────────────────────────────────────────────

def _simulate_option_close(eod: pd.DataFrame, option_id: int, entry_date: date,
                           exp_date: date, premium: float, close_scenario: str) -> dict:
    if option_id not in eod.index.get_level_values(0):
        return {
            'close_date':      str(exp_date),
            'close_price':     0.0,
            'pnl':             round(premium * 100, 2),
            'days_held':       (exp_date - entry_date).days,
            'is_expiry_close': True,
        }

    daily = eod.loc[option_id]
    daily = daily[(daily.index > entry_date) & (daily.index <= exp_date)]

    threshold = None
    if close_scenario == 'exit25':
        threshold = premium * 0.25
    elif close_scenario == 'exit50':
        threshold = premium * 0.50

    if threshold is not None:
        for td, pr in daily.iterrows():
            mark = float(pr['mark'])
            if mark <= threshold:
                return {
                    'close_date':      str(td),
                    'close_price':     round(mark, 4),
                    'pnl':             round((premium - mark) * 100, 2),
                    'days_held':       (td - entry_date).days,
                    'is_expiry_close': False,
                }

    exp_mark = 0.0
    if not daily.empty and exp_date in daily.index:
        exp_mark = max(float(daily.loc[exp_date, 'mark']), 0.0)
    return {
        'close_date':      str(exp_date),
        'close_price':     round(exp_mark, 4),
        'pnl':             round((premium - exp_mark) * 100, 2),
        'days_held':       (exp_date - entry_date).days,
        'is_expiry_close': True,
    }


def _adjust_to_trading_day(target: date, lower_bound: date, upper_bound: date) -> Optional[date]:
    for offset in [0, -1, 1, -2, 2, -3, 3]:
        d = target + timedelta(days=offset)
        if lower_bound <= d < upper_bound:
            return d
    return None


def _new_round(start_date_str: str) -> dict:
    return {
        'started':              start_date_str,
        'closed':               None,
        'closed_via':           None,
        'put_trades':           [],
        'call_trades':          [],
        'skipped_call_cycles':  0,
        'pnl':                  0.0,
        'assignment_strike':    None,
        'assignment_date':      None,
        'cost_basis':           None,
        'called_away_strike':   None,
        'called_away_date':     None,
        'stock_pnl':            None,
        'unrealized_stock_pnl': None,
        'mtm_price':            None,
        'mtm_date':             None,
    }


def _wheel_run_put(symbol, entry_date, exp_date, target_delta, target_dte,
                   min_iv, close_scenario, underlying_prices, iv_regime='all'):
    df = _find_entry_options(symbol, [(entry_date, exp_date)], target_delta, target_dte, 'P')
    if df.empty:
        return None, None

    if min_iv is not None and min_iv > 0:
        df = df[df['iv'] >= min_iv]
        if df.empty:
            return None, None

    if iv_regime == 'high':
        df = df[df['iv'] >= 0.18]
    elif iv_regime == 'low':
        df = df[df['iv'] < 0.18]
    if df.empty:
        return None, None

    row     = df.iloc[0]
    oid     = int(row['option_id'])
    premium = float(row['mark'])
    strike  = float(row['strike'])

    eod = _load_price_history([oid], entry_date, exp_date + timedelta(days=2))
    close_dict = _simulate_option_close(eod, oid, entry_date, exp_date, premium, close_scenario)

    underlying_at_exp = underlying_prices.get(exp_date)
    assigned = (
        close_dict['is_expiry_close']
        and underlying_at_exp is not None
        and underlying_at_exp < strike
    )
    cost_basis = round(strike - premium, 4) if assigned else None
    spy_at_entry = underlying_prices.get(entry_date)
    cb_discount = (
        round(spy_at_entry - cost_basis, 4)
        if assigned and spy_at_entry is not None and cost_basis is not None else None
    )

    leg = {
        'phase':               'PUT',
        'open_date':           str(entry_date),
        'expiry':              str(exp_date),
        'strike':              strike,
        'delta':               round(float(row['delta']), 3),
        'iv_at_open':          round(float(row['iv']), 4) if row['iv'] > 0 else None,
        'premium':             round(premium, 4),
        'premium_usd':         round(premium * 100, 2),
        'close_date':          close_dict['close_date'],
        'close_price':         close_dict['close_price'],
        'pnl':                 close_dict['pnl'],
        'days_held':           close_dict['days_held'],
        'is_expiry_close':     close_dict['is_expiry_close'],
        'assigned':            assigned,
        'cost_basis':          cost_basis,
        'cost_basis_discount': cb_discount,
    }
    return close_dict, leg


def _wheel_run_call(symbol, entry_date, exp_date, target_delta, target_dte,
                    min_iv, close_scenario, underlying_prices, cost_basis, iv_regime='all'):
    df = _find_entry_options(symbol, [(entry_date, exp_date)], target_delta, target_dte, 'C')
    if df.empty:
        return None, None

    sellable = df[df['strike'] >= cost_basis]
    if min_iv is not None and min_iv > 0 and not sellable.empty:
        sellable = sellable[sellable['iv'] >= min_iv]
    if iv_regime == 'high' and not sellable.empty:
        sellable = sellable[sellable['iv'] >= 0.18]
    elif iv_regime == 'low' and not sellable.empty:
        sellable = sellable[sellable['iv'] < 0.18]
    if sellable.empty:
        return None, {'skipped': True}

    sellable = sellable.sort_values('score')
    row     = sellable.iloc[0]
    oid     = int(row['option_id'])
    premium = float(row['mark'])
    strike  = float(row['strike'])

    eod = _load_price_history([oid], entry_date, exp_date + timedelta(days=2))
    close_dict = _simulate_option_close(eod, oid, entry_date, exp_date, premium, close_scenario)

    underlying_at_exp = underlying_prices.get(exp_date)
    called_away = (
        close_dict['is_expiry_close']
        and underlying_at_exp is not None
        and underlying_at_exp >= strike
    )

    leg = {
        'phase':              'CALL',
        'open_date':          str(entry_date),
        'expiry':             str(exp_date),
        'strike':             strike,
        'delta':              round(float(row['delta']), 3),
        'iv_at_open':         round(float(row['iv']), 4) if row['iv'] > 0 else None,
        'premium':            round(premium, 4),
        'premium_usd':        round(premium * 100, 2),
        'close_date':         close_dict['close_date'],
        'close_price':        close_dict['close_price'],
        'pnl':                close_dict['pnl'],
        'days_held':          close_dict['days_held'],
        'is_expiry_close':    close_dict['is_expiry_close'],
        'cost_basis_at_open': cost_basis,
        'called_away':        called_away,
    }
    return close_dict, leg


def run_wheel_simulation(symbol: str, start: date, end: date,
                         target_delta: float = 0.17, target_dte: int = 45,
                         min_iv: Optional[float] = None,
                         close_scenario: str = 'exitExp',
                         iv_regime: str = 'all') -> Optional[dict]:
    """
    Wheel strategy backtest: sell CSP → if assigned switch to CC with cost-basis-protective
    rule → if called away resume selling puts.
    """
    if close_scenario not in ('exit25', 'exit50', 'exitExp'):
        raise ValueError(f"close_scenario must be exit25/exit50/exitExp, got {close_scenario}")

    pairs = _monthly_entry_dates(start, end, target_dte)
    if not pairs:
        return None

    underlying_prices = _fetch_underlying_prices(symbol, start, end)

    phase          = 'PUT'
    cost_basis     = None
    shares_held    = 0
    available_from = start

    trades = []
    rounds = []
    cur_round = _new_round(str(start))

    for entry_target, exp_date in pairs:
        if entry_target < available_from:
            continue
        if exp_date > end:
            break

        entry_date = _adjust_to_trading_day(entry_target, max(available_from, start), exp_date)
        if entry_date is None:
            continue

        if phase == 'PUT':
            close_dict, leg = _wheel_run_put(
                symbol, entry_date, exp_date, target_delta, target_dte,
                min_iv, close_scenario, underlying_prices, iv_regime,
            )
            if leg is None:
                continue

            cur_round['put_trades'].append(leg)
            cur_round['pnl'] += leg['pnl']
            trades.append(leg)

            if leg.get('assigned') is True:
                phase       = 'CALL'
                cost_basis  = leg['cost_basis']
                shares_held = 100
                cur_round['assignment_strike'] = leg['strike']
                cur_round['assignment_date']   = leg['close_date']
                cur_round['cost_basis']        = cost_basis

            available_from = date.fromisoformat(close_dict['close_date']) + timedelta(days=1)

        else:  # CALL phase
            close_dict, leg = _wheel_run_call(
                symbol, entry_date, exp_date, target_delta, target_dte,
                min_iv, close_scenario, underlying_prices, cost_basis, iv_regime,
            )

            if leg is None or leg.get('skipped') is True:
                hold_record = {
                    'open_date':  str(entry_date),
                    'expiry':     str(exp_date),
                    'phase':      'HOLD',
                    'reason':     ('no_call_above_cost_basis' if leg and leg.get('skipped')
                                   else 'no_call_data'),
                    'cost_basis': cost_basis,
                    'pnl':        0.0,
                }
                cur_round['skipped_call_cycles'] += 1
                trades.append(hold_record)
                available_from = exp_date + timedelta(days=1)
                continue

            cur_round['call_trades'].append(leg)
            cur_round['pnl'] += leg['pnl']
            trades.append(leg)

            if leg.get('called_away') is True:
                stock_pnl = round((leg['strike'] - cost_basis) * 100, 2)
                cur_round['pnl']               += stock_pnl
                cur_round['called_away_strike'] = leg['strike']
                cur_round['called_away_date']   = leg['close_date']
                cur_round['stock_pnl']          = stock_pnl
                cur_round['closed']             = leg['close_date']
                cur_round['closed_via']         = 'called_away'

                trades.append({
                    'event':      'STOCK_CALLED_AWAY',
                    'date':       leg['close_date'],
                    'strike':     leg['strike'],
                    'cost_basis': cost_basis,
                    'pnl':        stock_pnl,
                })

                rounds.append(cur_round)
                cur_round   = _new_round(str(date.fromisoformat(leg['close_date']) + timedelta(days=1)))
                phase       = 'PUT'
                cost_basis  = None
                shares_held = 0

            available_from = date.fromisoformat(close_dict['close_date']) + timedelta(days=1)

    # End-of-backtest: mark stock to market if still holding
    if phase == 'CALL' and shares_held > 0:
        last_price, last_date = None, None
        for d in sorted(underlying_prices.keys(), reverse=True):
            if d <= end:
                last_price = underlying_prices[d]
                last_date  = d
                break
        if last_price is not None and cost_basis is not None:
            unrealized = round((last_price - cost_basis) * 100, 2)
            cur_round['pnl']                 += unrealized
            cur_round['unrealized_stock_pnl'] = unrealized
            cur_round['mtm_price']            = last_price
            cur_round['mtm_date']             = str(last_date)
            cur_round['closed_via']           = 'eob_mark_to_market'
            cur_round['closed']               = str(last_date)
            trades.append({
                'event':      'EOB_MARK_TO_MARKET',
                'date':       str(last_date),
                'price':      last_price,
                'cost_basis': cost_basis,
                'pnl':        unrealized,
            })

    if cur_round['put_trades'] or cur_round['call_trades']:
        rounds.append(cur_round)

    put_legs   = [t for t in trades if t.get('phase') == 'PUT']
    call_legs  = [t for t in trades if t.get('phase') == 'CALL']
    hold_legs  = [t for t in trades if t.get('phase') == 'HOLD']
    stock_evts = [t for t in trades if t.get('event') in ('STOCK_CALLED_AWAY', 'EOB_MARK_TO_MARKET')]

    total_option_pnl = sum(t.get('pnl', 0) for t in put_legs + call_legs)
    total_stock_pnl  = sum(t.get('pnl', 0) for t in stock_evts)
    total_pnl        = total_option_pnl + total_stock_pnl
    n_options        = len(put_legs) + len(call_legs)
    n_assignments    = sum(1 for t in put_legs if t.get('assigned') is True)
    n_called_away    = sum(1 for t in call_legs if t.get('called_away') is True)
    win_rate_options = (
        round(sum(1 for t in put_legs + call_legs if t.get('pnl', 0) > 0) / n_options * 100, 1)
        if n_options > 0 else 0.0
    )

    curve_dates, curve_pnl = [], []
    cum = 0.0
    for ev in sorted(trades, key=lambda t: t.get('close_date') or t.get('date') or t.get('open_date', '')):
        cum += ev.get('pnl', 0)
        curve_dates.append(ev.get('close_date') or ev.get('date') or ev.get('open_date'))
        curve_pnl.append(round(cum, 2))

    return {
        'trades': trades,
        'rounds': rounds,
        'equity_curve': {'dates': curve_dates, 'cum_pnl': curve_pnl},
        'stats': {
            'close_scenario':          close_scenario,
            'target_delta':            target_delta,
            'target_dte':              target_dte,
            'num_put_trades':          len(put_legs),
            'num_call_trades':         len(call_legs),
            'num_skipped_call_cycles': len(hold_legs),
            'num_assignments':         n_assignments,
            'num_called_away':         n_called_away,
            'num_complete_rounds':     sum(1 for r in rounds if r.get('closed_via') == 'called_away'),
            'put_assignment_rate':     round(n_assignments / len(put_legs) * 100, 1) if put_legs else 0.0,
            'call_assignment_rate':    round(n_called_away / len(call_legs) * 100, 1) if call_legs else 0.0,
            'win_rate_options':        win_rate_options,
            'total_option_pnl':        round(total_option_pnl, 2),
            'total_stock_pnl':         round(total_stock_pnl, 2),
            'total_pnl':               round(total_pnl, 2),
            'avg_round_pnl':           round(sum(r['pnl'] for r in rounds) / len(rounds), 2) if rounds else 0.0,
            'final_phase':             phase,
            'final_cost_basis':        cost_basis,
            'final_shares_held':       shares_held,
        },
    }
