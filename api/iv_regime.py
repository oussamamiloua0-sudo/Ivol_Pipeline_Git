"""
IV Regime calculator.
Pulls last 12 months of ATM call IV from fact_option_eod.
Returns current IV, percentile rank, and monthly history.
"""
from datetime import date, timedelta
from .db import query


def get_iv_regime(symbol: str):
    end_date = date.today()
    start_date = end_date - timedelta(days=365)

    sql = """
        SELECT
            f.trade_date,
            AVG(f.iv) AS avg_iv
        FROM fact_option_eod f
        JOIN dim_option_contract o USING(option_id)
        JOIN dim_underlying u USING(underlying_id)
        WHERE u.symbol = :symbol
          AND f.trade_date BETWEEN :start AND :end
          AND o.call_put = 'C'
          AND f.delta BETWEEN 0.45 AND 0.55
          AND f.iv > 0
        GROUP BY f.trade_date
        ORDER BY f.trade_date
    """
    rows = query(sql, {'symbol': symbol, 'start': start_date, 'end': end_date})

    if not rows:
        return None

    daily_ivs = [(str(r['trade_date']), float(r['avg_iv'])) for r in rows]
    iv_values = [v for _, v in daily_ivs]

    current_iv = iv_values[-1] if iv_values else 0
    percentile = round(
        sum(1 for v in iv_values if v <= current_iv) / len(iv_values) * 100, 1
    )

    # Monthly averages for the chart
    monthly = {}
    for d_str, iv in daily_ivs:
        month_key = d_str[:7]  # YYYY-MM
        if month_key not in monthly:
            monthly[month_key] = []
        monthly[month_key].append(iv)

    history = [
        {'date': month, 'iv': round(sum(ivs) / len(ivs) * 100, 2)}
        for month, ivs in sorted(monthly.items())
    ]

    return {
        'symbol': symbol,
        'current_iv': round(current_iv * 100, 2),
        'percentile': percentile,
        'regime': (
            'Low' if percentile < 25 else
            'Normal' if percentile < 75 else
            'High'
        ),
        'history': history,
    }
