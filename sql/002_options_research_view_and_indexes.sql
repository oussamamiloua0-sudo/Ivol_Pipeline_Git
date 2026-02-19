-- 002_options_research_view_and_indexes.sql
-- NOTE: Use backticks for identifiers and single quotes for strings.

-- Index: fact_option_eod.trade_date
SET @idx_exists := (
  SELECT COUNT(1)
  FROM INFORMATION_SCHEMA.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'fact_option_eod'
    AND INDEX_NAME = 'ix_fact_option_eod_trade_date'
);
SET @sql := IF(
  @idx_exists = 0,
  'CREATE INDEX `ix_fact_option_eod_trade_date` ON `fact_option_eod` (`trade_date`)',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Index: dim_option_contract (underlying_id, expiration_date, strike, call_put)
SET @idx_exists := (
  SELECT COUNT(1)
  FROM INFORMATION_SCHEMA.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'dim_option_contract'
    AND INDEX_NAME = 'ix_dim_option_contract_underlying_exp_strike_cp'
);
SET @sql := IF(
  @idx_exists = 0,
  'CREATE INDEX `ix_dim_option_contract_underlying_exp_strike_cp` ON `dim_option_contract` (`underlying_id`,`expiration_date`,`strike`,`call_put`)',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Research view (contract-level options + underlying symbol)
CREATE OR REPLACE VIEW `v_option_eod_research` AS
SELECT
  o.`trade_date` AS `trade_date`,
  u.`symbol` AS `underlying_symbol`,
  c.`expiration_date` AS `expiration_date`,
  c.`strike` AS `strike`,
  c.`call_put` AS `option_type`,
  o.`bid` AS `bid`,
  o.`ask` AS `ask`,
  CASE
    WHEN o.`bid` IS NOT NULL AND o.`ask` IS NOT NULL
      THEN (o.`bid` + o.`ask`) / 2
    ELSE NULL
  END AS `mid_price`,
  o.`price` AS `close_price`,
  o.`volume` AS `volume`,
  o.`open_interest` AS `open_interest`,
  o.`iv` AS `iv`,
  o.`preiv` AS `preiv`,
  o.`delta` AS `delta`,
  o.`gamma` AS `gamma`,
  o.`theta` AS `theta`,
  o.`vega` AS `vega`,
  o.`rho` AS `rho`,
  o.`is_settlement` AS `is_settlement`,
  o.`option_id` AS `option_id`,
  c.`option_symbol` AS `option_symbol`
FROM `fact_option_eod` o
JOIN `dim_option_contract` c ON c.`option_id` = o.`option_id`
JOIN `dim_underlying` u ON u.`underlying_id` = c.`underlying_id`;
