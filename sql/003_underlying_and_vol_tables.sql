-- 003_underlying_and_vol_tables.sql
-- NOTE: Use backticks for identifiers and single quotes for strings.

-- fact_underlying_eod
CREATE TABLE IF NOT EXISTS `fact_underlying_eod` (
  `underlying_id` BIGINT NOT NULL,
  `trade_date` DATE NOT NULL,
  `open` DECIMAL(12,6) NULL,
  `high` DECIMAL(12,6) NULL,
  `low` DECIMAL(12,6) NULL,
  `close` DECIMAL(12,6) NULL,
  `adj_close` DECIMAL(12,6) NULL,
  `volume` BIGINT NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`underlying_id`, `trade_date`),
  CONSTRAINT `fk_fact_underlying_eod_underlying_id`
    FOREIGN KEY (`underlying_id`) REFERENCES `dim_underlying`(`underlying_id`)
    ON DELETE RESTRICT ON UPDATE CASCADE
);

-- Index: fact_underlying_eod.trade_date
SET @idx_exists := (
  SELECT COUNT(1)
  FROM INFORMATION_SCHEMA.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'fact_underlying_eod'
    AND INDEX_NAME = 'ix_fact_underlying_eod_trade_date'
);
SET @sql := IF(
  @idx_exists = 0,
  'CREATE INDEX `ix_fact_underlying_eod_trade_date` ON `fact_underlying_eod` (`trade_date`)',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- fact_vol_metrics
CREATE TABLE IF NOT EXISTS `fact_vol_metrics` (
  `underlying_id` BIGINT NOT NULL,
  `trade_date` DATE NOT NULL,
  `ivx` DOUBLE NULL,
  `hv_10` DOUBLE NULL,
  `hv_20` DOUBLE NULL,
  `hv_30` DOUBLE NULL,
  `hv_60` DOUBLE NULL,
  `hv_90` DOUBLE NULL,
  `hv_252` DOUBLE NULL,
  `ivx_raw` JSON NULL,
  `hv_raw` JSON NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`underlying_id`, `trade_date`),
  CONSTRAINT `fk_fact_vol_metrics_underlying_id`
    FOREIGN KEY (`underlying_id`) REFERENCES `dim_underlying`(`underlying_id`)
    ON DELETE RESTRICT ON UPDATE CASCADE
);

-- Index: fact_vol_metrics.trade_date
SET @idx_exists := (
  SELECT COUNT(1)
  FROM INFORMATION_SCHEMA.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'fact_vol_metrics'
    AND INDEX_NAME = 'ix_fact_vol_metrics_trade_date'
);
SET @sql := IF(
  @idx_exists = 0,
  'CREATE INDEX `ix_fact_vol_metrics_trade_date` ON `fact_vol_metrics` (`trade_date`)',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
