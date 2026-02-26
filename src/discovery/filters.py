"""Contract-list post-filters applied after discovery, before rawiv calls."""
from __future__ import annotations

import math
from datetime import date


# ---------------------------------------------------------------------------
# Monthly-expiry filter
# ---------------------------------------------------------------------------

def is_monthly_expiry(exp: date) -> bool:
    """Return True if *exp* is a standard monthly expiration.

    Rule: the 3rd Friday of the calendar month, OR the Saturday
    immediately following it (some providers record settlement as Sat).

    3rd Friday occupies days 15-21 of the month (proof: earliest 1st
    Friday is day 1 → 3rd Friday is day 15; latest 1st Friday is day 7
    → 3rd Friday is day 21).  The Saturday after occupies days 16-22.
    """
    dow = exp.weekday()   # 0=Mon … 4=Fri … 5=Sat
    day = exp.day
    return (dow == 4 and 15 <= day <= 21) or (dow == 5 and 16 <= day <= 22)


def filter_monthlies(contracts: list[dict]) -> tuple[list[dict], int]:
    """Keep only contracts whose expirationDate is a monthly expiry.

    Returns (kept, dropped_count).
    Each contract dict must have an 'expirationDate' key in YYYY-MM-DD format.
    """
    kept:    list[dict] = []
    dropped: int        = 0
    for c in contracts:
        raw_exp = c.get("expirationDate") or c.get("expiration_date") or ""
        try:
            exp = date.fromisoformat(str(raw_exp)[:10])
        except ValueError:
            dropped += 1
            continue
        if is_monthly_expiry(exp):
            kept.append(c)
        else:
            dropped += 1
    return kept, dropped


# ---------------------------------------------------------------------------
# Delta filter (Black-Scholes approximation — no rawiv calls needed)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf — no external dependencies."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_approx_delta(
    spot: float,
    strike: float,
    dte_days: int,
    call_put: str,
    sigma: float = 0.20,
    r: float = 0.05,
) -> float:
    """Black-Scholes approximate delta using a fixed vol assumption.

    Args:
        spot:     Underlying spot close price.
        strike:   Option strike price.
        dte_days: Calendar days to expiration (clamped to >= 1).
        call_put: "C" for call, "P" for put (case-insensitive, first char used).
        sigma:    Annualized implied vol assumption (default 0.20 = 20%).
        r:        Risk-free rate assumption (default 0.05 = 5%).

    Returns:
        Approximate delta.  Calls in [0, 1], puts in [-1, 0].

    Note:
        This is a pre-rawiv approximation only.  Actual delta comes from the
        rawiv endpoint and may differ if the realized vol deviates from sigma.
    """
    T  = max(dte_days, 1) / 365.0
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    nd1 = _norm_cdf(d1)
    return nd1 if call_put.upper()[0] == "C" else nd1 - 1.0


def filter_by_delta(
    contracts: list[dict],
    spot: float,
    trade_date: date,
    delta_low: float,
    delta_high: float,
    sigma: float = 0.20,
) -> tuple[list[dict], int]:
    """Keep contracts whose BS-approximate delta is in [delta_low, delta_high].

    Delta is computed using a fixed-vol Black-Scholes approximation (no rawiv
    calls required).  This is an additive filter: apply AFTER the existing
    strike-band and monthlies filters, not instead of them.

    Args:
        contracts:  List of contract dicts from discovery.
        spot:       Underlying spot close for trade_date.
        trade_date: The trading date (used to compute DTE).
        delta_low:  Lower bound (inclusive).  Put deltas are negative.
        delta_high: Upper bound (inclusive).
        sigma:      Annualized vol assumption passed to bs_approx_delta.

    Returns:
        (kept, dropped_count)
    """
    kept:    list[dict] = []
    dropped: int        = 0
    for c in contracts:
        raw_exp  = c.get("expirationDate") or c.get("expiration_date") or ""
        call_put = (c.get("callPut") or c.get("call_put") or "C").upper()[0]
        try:
            exp      = date.fromisoformat(str(raw_exp)[:10])
            dte_days = max((exp - trade_date).days, 1)
            strike   = float(c.get("strike") or 0)
            if strike <= 0 or spot <= 0:
                dropped += 1
                continue
        except (ValueError, TypeError):
            dropped += 1
            continue
        delta = bs_approx_delta(spot, strike, dte_days, call_put, sigma)
        if delta_low <= delta <= delta_high:
            kept.append(c)
        else:
            dropped += 1
    return kept, dropped
