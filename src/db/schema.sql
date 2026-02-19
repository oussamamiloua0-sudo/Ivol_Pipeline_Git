-- =========================
-- Dimensions
-- =========================

CREATE TABLE IF NOT EXISTS dim_underlying (
    underlying_id BIGSERIAL PRIMARY KEY,
    symbol        TEXT NOT NULL UNIQUE,
    exchange      TEXT NULL,
    currency      TEXT NULL,
    active_from   DATE NULL,
    active_to     DATE NULL,
    is_delisted   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dim_option_contract (
    option_id        BIGSERIAL PRIMARY KEY,
    underlying_id    BIGINT NOT NULL REFERENCES dim_underlying(underlying_id),
    expiration_date  DATE NOT NULL,
    strike           NUMERIC(14,4) NOT NULL,
    call_put         CHAR(1) NOT NULL CHECK (call_put IN ('C','P')),
    style            CHAR(1) NULL,              -- optional (A=American, E=European) if provided
    multiplier       INTEGER NULL,              -- optional, often 100
    option_symbol    TEXT NULL,                 -- OCC / vendor symbol if available
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (underlying_id, expiration_date, strike, call_put, style)
);

CREATE INDEX IF NOT EXISTS ix_option_contract_underlying_exp
    ON dim_option_contract (underlying_id, expiration_date);

-- =========================
-- Facts: Underlying EOD
-- =========================

CREATE TABLE IF NOT EXISTS fact_underlying_eod (
    trade_date     DATE NOT NULL,
    underlying_id  BIGINT NOT NULL REFERENCES dim_underlying(underlying_id),
    open           NUMERIC(18,6) NULL,
    high           NUMERIC(18,6) NULL,
    low            NUMERIC(18,6) NULL,
    close          NUMERIC(18,6) NULL,
    adj_close      NUMERIC(18,6) NULL,
    volume         BIGINT NULL,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, underlying_id)
);

CREATE INDEX IF NOT EXISTS ix_underlying_eod_underlying_date
    ON fact_underlying_eod (underlying_id, trade_date);

-- =========================
-- Facts: Option EOD (contract-level)
-- =========================

CREATE TABLE IF NOT EXISTS fact_option_eod (
    trade_date      DATE NOT NULL,
    option_id       BIGINT NOT NULL REFERENCES dim_option_contract(option_id),

    bid             NUMERIC(18,6) NULL,
    ask             NUMERIC(18,6) NULL,
    mid             NUMERIC(18,6) NULL,
    close           NUMERIC(18,6) NULL,         -- or settlement if that’s what vendor provides
    volume          BIGINT NULL,
    open_interest   BIGINT NULL,

    iv              DOUBLE PRECISION NULL,
    delta           DOUBLE PRECISION NULL,
    gamma           DOUBLE PRECISION NULL,
    theta           DOUBLE PRECISION NULL,
    vega            DOUBLE PRECISION NULL,
    rho             DOUBLE PRECISION NULL,

    underlying_close NUMERIC(18,6) NULL,        -- optional denormalized snapshot for speed
    src_timestamp   TIMESTAMPTZ NULL,           -- vendor timestamp if present

    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, option_id)
);

CREATE INDEX IF NOT EXISTS ix_option_eod_option_date
    ON fact_option_eod (option_id, trade_date);

CREATE INDEX IF NOT EXISTS ix_option_eod_date
    ON fact_option_eod (trade_date);

-- =========================
-- Optional: Volatility context (can be empty at first)
-- =========================

CREATE TABLE IF NOT EXISTS fact_vol_metrics (
    trade_date     DATE NOT NULL,
    underlying_id  BIGINT NOT NULL REFERENCES dim_underlying(underlying_id),

    rv_10          DOUBLE PRECISION NULL,
    rv_20          DOUBLE PRECISION NULL,
    rv_60          DOUBLE PRECISION NULL,

    atm_iv_30      DOUBLE PRECISION NULL,
    atm_iv_60      DOUBLE PRECISION NULL,
    atm_iv_90      DOUBLE PRECISION NULL,

    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, underlying_id)
);

CREATE INDEX IF NOT EXISTS ix_vol_metrics_underlying_date
    ON fact_vol_metrics (underlying_id, trade_date);
