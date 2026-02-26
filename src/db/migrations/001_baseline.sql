-- Migration 001 — baseline schema (idempotent, matches live DO MySQL DB)
-- Run once to create tables if they don't exist yet.
-- If tables already exist with the right structure, this is a no-op.

-- DigitalOcean managed MySQL connection (PowerShell):
--   mysql --host=db-mysql-nyc3-06366-do-user-33453591-0.e.db.ondigitalocean.com \
--         --port=25060 --user=ivol_app --password --ssl-mode=REQUIRED \
--         --database=ivol < src\db\migrations\001_baseline.sql

SOURCE src/db/schema.sql;
