-- 004_joined_research_view.sql
-- NOTE: Use backticks for identifiers and single quotes for strings.

CREATE OR REPLACE VIEW `v_research_option_underlying_vol` AS
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
  c.`option_symbol` AS `option_symbol`,
  fe.`open` AS `underlying_open`,
  fe.`high` AS `underlying_high`,
  fe.`low` AS `underlying_low`,
  fe.`close` AS `underlying_close`,
  fe.`volume` AS `underlying_volume`,
  fv.`ivx` AS `ivx`,
  fv.`hv10` AS `hv10`,
  fv.`hv20` AS `hv20`,
  fv.`hv30` AS `hv30`,
  fv.`hv60` AS `hv60`,
  fv.`hv90` AS `hv90`
FROM `fact_option_eod` o
JOIN `dim_option_contract` c ON c.`option_id` = o.`option_id`
JOIN `dim_underlying` u ON u.`underlying_id` = c.`underlying_id`
LEFT JOIN `fact_underlying_eod` fe
  ON fe.`underlying_id` = c.`underlying_id`
 AND fe.`trade_date` = o.`trade_date`
LEFT JOIN `fact_vol_metrics` fv
  ON fv.`underlying_id` = c.`underlying_id`
 AND fv.`trade_date` = o.`trade_date`;
