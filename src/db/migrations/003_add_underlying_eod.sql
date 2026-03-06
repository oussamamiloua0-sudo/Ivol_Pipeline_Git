-- Migration 003: Add underlying EOD price table
-- Used by the portfolio analytics webapp to compute returns, beta, etc.
-- Populated by scripts/ingest_prices.py (run daily after market close)

CREATE TABLE IF NOT EXISTS fact_underlying_eod (
    underlying_id  BIGINT       NOT NULL,
    trade_date     DATE         NOT NULL,
    close_price    DECIMAL(12,4) NOT NULL,
    open_price     DECIMAL(12,4),
    high_price     DECIMAL(12,4),
    low_price      DECIMAL(12,4),
    volume         BIGINT,
    created_at     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (underlying_id, trade_date),
    CONSTRAINT fk_eod_underlying
        FOREIGN KEY (underlying_id) REFERENCES dim_underlying (underlying_id)
);

CREATE INDEX IF NOT EXISTS idx_underlying_eod_date
    ON fact_underlying_eod (trade_date);
