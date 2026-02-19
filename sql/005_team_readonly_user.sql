-- 005_team_readonly_user.sql
-- NOTE: Use backticks for identifiers and single quotes for strings.

CREATE USER IF NOT EXISTS 'sqilled_support'@'%' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';
GRANT SELECT ON `ivol`.* TO 'sqilled_support'@'%';
FLUSH PRIVILEGES;
