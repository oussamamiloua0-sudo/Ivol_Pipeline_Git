"""SchemaCache: one-time MetaData.reflect() passed into every ingest call."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import MetaData, Table
from sqlalchemy.engine import Engine


@dataclass
class ChainResult:
    date: str
    symbol: str
    discovered_calls: int        = 0
    discovered_puts: int         = 0
    contracts_total: int         = 0
    contracts_new: int           = 0
    contracts_skipped_known: int = 0
    loaded_ok: int               = 0
    loaded_failed: int           = 0
    skipped: int                 = 0
    elapsed_seconds: float       = 0.0
    avg_rps: float               = 0.0
    error: Optional[str]         = None
    success: bool                = True
    contract_errors: list[dict]  = field(default_factory=list)


@dataclass
class SchemaCache:
    """Reflected table objects + detected column names. Built once at startup."""
    tbl_u: Table    # dim_underlying
    tbl_c: Table    # dim_option_contract
    tbl_f: Table    # fact_option_eod

    u_id_col: str
    u_sym_col: str

    c_oid: str
    c_uid: Optional[str]
    c_exp: Optional[str]
    c_sym: Optional[str]
    c_exch: Optional[str]
    c_osym: Optional[str]
    c_strike: Optional[str]
    c_cp: Optional[str]
    c_style: Optional[str]

    f_oid: str
    f_date: str
    f_bid: Optional[str]
    f_ask: Optional[str]
    f_price: Optional[str]
    f_iv: Optional[str]
    f_preiv: Optional[str]
    f_delta: Optional[str]
    f_gamma: Optional[str]
    f_vega: Optional[str]
    f_theta: Optional[str]
    f_rho: Optional[str]
    f_vol: Optional[str]
    f_oi: Optional[str]
    f_settle: Optional[str]


def build_schema_cache(engine: Engine) -> SchemaCache:
    """Reflect the three pipeline tables and detect all column names.
    Call once at backfill startup; pass the result into every ingest call."""
    md = MetaData()
    md.reflect(bind=engine, only=["dim_underlying", "dim_option_contract", "fact_option_eod"])

    tbl_u = md.tables.get("dim_underlying")
    tbl_c = md.tables.get("dim_option_contract")
    tbl_f = md.tables.get("fact_option_eod")

    if tbl_u is None or tbl_c is None or tbl_f is None:
        raise RuntimeError(
            f"Missing tables. Found: {sorted(md.tables)}. "
            "Expected: dim_underlying, dim_option_contract, fact_option_eod"
        )

    def col(tbl: Table, candidates: list[str]) -> Optional[str]:
        for c in candidates:
            if c in tbl.c:
                return c
        return None

    u_id_col  = col(tbl_u, ["underlying_id", "id"])
    u_sym_col = col(tbl_u, ["symbol", "ticker"])
    if not u_sym_col:
        raise RuntimeError("dim_underlying has no symbol/ticker column.")

    c_oid = col(tbl_c, ["option_id", "optionId"])
    if not c_oid:
        raise RuntimeError("dim_option_contract has no option_id column.")

    f_oid  = col(tbl_f, ["option_id", "optionId"])
    f_date = col(tbl_f, ["trade_date", "date", "asof_date"])
    if not f_oid or not f_date:
        raise RuntimeError("fact_option_eod missing required option_id / trade_date column.")

    return SchemaCache(
        tbl_u=tbl_u, tbl_c=tbl_c, tbl_f=tbl_f,
        u_id_col=u_id_col,   u_sym_col=u_sym_col,
        c_oid=c_oid,
        c_uid=col(tbl_c,   ["underlying_id"]),
        c_exp=col(tbl_c,   ["expiration_date", "expiration", "expiry"]),
        c_sym=col(tbl_c,   ["symbol"]),
        c_exch=col(tbl_c,  ["exchange"]),
        c_osym=col(tbl_c,  ["option_symbol"]),
        c_strike=col(tbl_c,["strike"]),
        c_cp=col(tbl_c,    ["call_put"]),
        c_style=col(tbl_c, ["style"]),
        f_oid=f_oid,         f_date=f_date,
        f_bid=col(tbl_f,   ["bid"]),
        f_ask=col(tbl_f,   ["ask"]),
        f_price=col(tbl_f, ["price", "mid"]),
        f_iv=col(tbl_f,    ["iv"]),
        f_preiv=col(tbl_f, ["preiv"]),
        f_delta=col(tbl_f, ["delta"]),
        f_gamma=col(tbl_f, ["gamma"]),
        f_vega=col(tbl_f,  ["vega"]),
        f_theta=col(tbl_f, ["theta"]),
        f_rho=col(tbl_f,   ["rho"]),
        f_vol=col(tbl_f,   ["volume"]),
        f_oi=col(tbl_f,    ["open_interest"]),
        f_settle=col(tbl_f,["is_settlement"]),
    )
