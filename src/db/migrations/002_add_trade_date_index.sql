-- Migration 002 — add trade_date index on fact_option_eod (idempotent)
-- Without this, exports filtered by trade_date do a full index scan on
-- the (option_id, trade_date) PK instead of a fast range seek on date.
--
-- Run from PowerShell:
--   mysql --host=db-mysql-nyc3-06366-do-user-33453591-0.e.db.ondigitalocean.com \
--         --port=25060 --user=ivol_app --password --ssl-mode=REQUIRED \
--         --database=ivol < src\db\migrations\002_add_trade_date_index.sql

-- MySQL 5.7+: IF EXISTS guard via procedure trick
-- Simpler: just run it; harmless if the index already exists via schema.sql.
ALTER TABLE fact_option_eod
    ADD KEY IF NOT EXISTS ix_fact_option_eod_trade_date (trade_date);
