"""DB write helpers: bulk upsert + underlying dimension management."""
from __future__ import annotations

from datetime import date as dt_date
from typing import Any, Iterator, Optional

from sqlalchemy import Table, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from src.ingest.schema_cache import SchemaCache


def chunked(lst: list, size: int) -> Iterator[list]:
    for i in range(0, len(lst), size):
        yield lst[i: i + size]


def bulk_upsert(conn: Any, tbl: Table, rows: list[dict], conflict_cols: list[str]) -> int:
    """INSERT ... ON DUPLICATE KEY UPDATE for a list of row dicts.
    Caller manages the transaction.  Returns len(rows)."""
    if not rows:
        return 0
    all_keys   = sorted({k for row in rows for k in row if k in tbl.c})
    if not all_keys:
        return 0
    normalized = [{k: row.get(k) for k in all_keys} for row in rows]
    stmt       = mysql_insert(tbl).values(normalized)
    update_map = {k: stmt.inserted[k] for k in all_keys if k not in conflict_cols}
    stmt       = stmt.on_duplicate_key_update(**update_map) if update_map else stmt.prefix_with("IGNORE")
    conn.execute(stmt)
    return len(normalized)


def ensure_underlying(conn: Any, sc: SchemaCache, symbol: str) -> int:
    """Return underlying_id for symbol, inserting if absent."""
    tbl_u   = sc.tbl_u
    id_col  = sc.u_id_col
    sym_col = sc.u_sym_col

    if id_col:
        row = conn.execute(
            select(tbl_u.c[id_col]).where(tbl_u.c[sym_col] == symbol).limit(1)
        ).scalar_one_or_none()
        if row is not None:
            return int(row)

    ins = mysql_insert(tbl_u).values(**{sym_col: symbol})
    conn.execute(ins.prefix_with("IGNORE"))

    if not id_col:
        raise RuntimeError("dim_underlying has no id column; cannot resolve underlying_id.")

    return int(
        conn.execute(
            select(tbl_u.c[id_col]).where(tbl_u.c[sym_col] == symbol).limit(1)
        ).scalar_one()
    )


def coerce_date(v: Any) -> Optional[dt_date]:
    if v is None:
        return None
    if isinstance(v, dt_date):
        return v
    s = str(v).strip()
    return dt_date.fromisoformat(s[:10]) if s else None
