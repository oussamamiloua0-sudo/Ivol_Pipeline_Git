-- =============================================================================
-- ivolatility-data-pipeline  —  canonical MySQL schema (DO Managed MySQL)
-- Matches the live DB exactly.  Safe to re-run (IF NOT EXISTS / IF NOT EXISTS KEY).
-- =============================================================================

CREATE TABLE IF NOT EXISTS dim_underlying (
    underlying_id  BIGINT        NOT NULL AUTO_INCREMENT,
    symbol         VARCHAR(32)   NOT NULL,
    created_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (underlying_id),
    UNIQUE KEY uq_underlying_symbol (symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS dim_option_contract (
    option_id       BIGINT         NOT NULL,
    underlying_id   BIGINT         NOT NULL,
    expiration_date DATE           NOT NULL,
    strike          DECIMAL(12,4)  NOT NULL,
    call_put        CHAR(1)        NOT NULL,   -- 'C' or 'P'
    style           CHAR(1)        NULL,       -- 'E' European | 'A' American
    option_symbol   VARCHAR(64)    NOT NULL,
    created_at      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (option_id),
    KEY ix_contract_underlying_exp (underlying_id, expiration_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS fact_option_eod (
    option_id      BIGINT          NOT NULL,
    trade_date     DATE            NOT NULL,

    bid            DECIMAL(12,6)   NULL,
    ask            DECIMAL(12,6)   NULL,
    price          DECIMAL(12,6)   NULL,

    iv             DOUBLE          NULL,
    preiv          DOUBLE          NULL,
    delta          DOUBLE          NULL,
    gamma          DOUBLE          NULL,
    vega           DOUBLE          NULL,
    theta          DOUBLE          NULL,
    rho            DOUBLE          NULL,

    volume         BIGINT          NULL,
    open_interest  BIGINT          NULL,
    is_settlement  TINYINT(1)      NULL,

    created_at     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (option_id, trade_date),
    KEY ix_fact_option_eod_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
