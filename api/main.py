"""
sqilled Options Tool — FastAPI backend
Runs on port 8000 (localhost dev) or droplet (prod)
"""
from datetime import date
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yfinance as yf

from .simulate import run_simulation, run_grid, run_wheel_simulation
from .iv_regime import get_iv_regime

app = FastAPI(title="sqilled Options API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Vercel proxy only — tighten in prod
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health check ────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


# ── Simulate covered call backtest ─────────────────────────────────────────
class SimulateRequest(BaseModel):
    symbol: str = "SPY"
    delta: float = 0.25       # target delta absolute value e.g. 0.17 - 0.50
    dte: int = 45             # target DTE: 30, 45, or 60
    start: date = date(2022, 1, 1)
    end: date = date(2024, 12, 31)
    strategy: str = "cc"     # 'cc' = covered call, 'csp' = cash-secured put
    iv_regime: str = "all"   # 'all', 'high' (>=18%), 'low' (<18%)


@app.post("/simulate")
def simulate(req: SimulateRequest):
    if req.symbol not in ("SPY", "QQQ", "IWM"):
        raise HTTPException(400, "Symbol must be SPY, QQQ, or IWM")
    if not (0.10 <= req.delta <= 0.50):
        raise HTTPException(400, "Delta must be between 0.10 and 0.50")
    if req.dte not in (30, 45, 60):
        raise HTTPException(400, "DTE must be 30, 45, or 60")
    if (req.end - req.start).days < 30:
        raise HTTPException(400, "Date range must be at least 30 days")
    if req.strategy not in ("cc", "csp"):
        raise HTTPException(400, "Strategy must be 'cc' or 'csp'")
    if req.iv_regime not in ("all", "high", "low"):
        raise HTTPException(400, "iv_regime must be 'all', 'high', or 'low'")

    result = run_simulation(req.symbol, req.delta, req.dte, req.start, req.end, strategy=req.strategy, iv_regime=req.iv_regime)
    if result is None:
        raise HTTPException(404, f"No options data found for {req.symbol} in that date range")
    return result


# ── Grid: run all delta×DTE combos ──────────────────────────────────────────
class GridRequest(BaseModel):
    symbol: str = "SPY"
    start: date = date(2022, 1, 1)
    end: date = date(2024, 12, 31)
    strategy: str = "cc"     # 'cc' = covered call, 'csp' = cash-secured put
    iv_regime: str = "all"   # 'all', 'high' (>=18%), 'low' (<18%)


@app.post("/grid")
def grid(req: GridRequest):
    if req.symbol not in ("SPY", "QQQ", "IWM"):
        raise HTTPException(400, "Symbol must be SPY, QQQ, or IWM")
    if (req.end - req.start).days < 30:
        raise HTTPException(400, "Date range must be at least 30 days")
    if req.strategy not in ("cc", "csp"):
        raise HTTPException(400, "Strategy must be 'cc' or 'csp'")
    if req.iv_regime not in ("all", "high", "low"):
        raise HTTPException(400, "iv_regime must be 'all', 'high', or 'low'")

    result = run_grid(req.symbol, req.start, req.end, strategy=req.strategy, iv_regime=req.iv_regime)
    if not result or not result.get("grid"):
        raise HTTPException(404, f"No grid data found for {req.symbol} in that date range")
    return result


# ── Wheel strategy backtest ─────────────────────────────────────────────────
class WheelRequest(BaseModel):
    symbol: str = "SPY"
    start: date = date(2022, 1, 1)
    end: date = date(2024, 12, 31)
    target_delta: float = 0.17
    target_dte: int = 45
    close_scenario: str = "exitExp"
    iv_regime: str = "all"   # 'all', 'high' (>=18%), 'low' (<18%)


@app.post("/wheel")
def wheel(req: WheelRequest):
    if req.symbol not in ("SPY", "QQQ", "IWM"):
        raise HTTPException(400, "Symbol must be SPY, QQQ, or IWM")
    if not (0.10 <= req.target_delta <= 0.50):
        raise HTTPException(400, "Delta must be between 0.10 and 0.50")
    if req.target_dte not in (30, 45, 60):
        raise HTTPException(400, "DTE must be 30, 45, or 60")
    if (req.end - req.start).days < 30:
        raise HTTPException(400, "Date range must be at least 30 days")
    if req.close_scenario not in ("exit25", "exit50", "exitExp"):
        raise HTTPException(400, "close_scenario must be exit25, exit50, or exitExp")
    if req.iv_regime not in ("all", "high", "low"):
        raise HTTPException(400, "iv_regime must be 'all', 'high', or 'low'")

    result = run_wheel_simulation(
        req.symbol, req.start, req.end,
        target_delta=req.target_delta,
        target_dte=req.target_dte,
        close_scenario=req.close_scenario,
        iv_regime=req.iv_regime,
    )
    if result is None:
        raise HTTPException(404, f"No options data found for {req.symbol} in that date range")
    return result


# ── IV Regime ───────────────────────────────────────────────────────────────
@app.get("/iv-regime")
def iv_regime(symbol: str = "SPY"):
    if symbol not in ("SPY", "QQQ", "IWM"):
        raise HTTPException(400, "Symbol must be SPY, QQQ, or IWM")
    result = get_iv_regime(symbol)
    if result is None:
        raise HTTPException(404, f"No IV data found for {symbol}")
    return result


# ── Equity prices (yfinance) ────────────────────────────────────────────────
@app.get("/prices")
def prices(symbol: str, start: date, end: date):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=str(start), end=str(end))
        if df.empty:
            raise HTTPException(404, f"No price data found for {symbol}")
        return {
            "symbol": symbol,
            "dates": [str(d.date()) for d in df.index],
            "close": [round(float(v), 4) for v in df["Close"]],
            "open":  [round(float(v), 4) for v in df["Open"]],
            "high":  [round(float(v), 4) for v in df["High"]],
            "low":   [round(float(v), 4) for v in df["Low"]],
            "volume": [int(v) for v in df["Volume"]],
        }
    except Exception as e:
        raise HTTPException(500, str(e))
