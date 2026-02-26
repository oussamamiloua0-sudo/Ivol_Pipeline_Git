"""Write the canonical export DataFrame to .xlsx or .tsv."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_excel(df: pd.DataFrame, path: str | Path, sheet_name: str | None = None) -> Path:
    out  = Path(path)
    name = sheet_name or out.stem[:31]   # Excel sheet name max 31 chars
    df.to_excel(out, index=False, sheet_name=name)
    return out


def write_tsv(df: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    df.to_csv(out, index=False, sep="\t")
    return out
