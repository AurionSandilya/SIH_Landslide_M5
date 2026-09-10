-- Architecture §34 / Phase 9: alert_audit_log is append-only. M5's
-- application DB role gets INSERT + SELECT only - no UPDATE/DELETE -
-- enforced by Postgres, not merely by application code discipline.
--
-- Assumes the role named by :app_role already exists (created by
-- scripts/apply_audit_role_migration.sh, since CREATE ROLE has no
-- IF NOT EXISTS form and psql does not interpolate :variables inside
-- dollar-quoted / DO blocks).
--
-- Invoke via scripts/apply_audit_role_migration.sh, not directly.

GRANT USAGE ON SCHEMA public TO :app_role;

-- Full CRUD on every M5-owned table EXCEPT the audit log.
GRANT SELECT, INSERT, UPDATE, DELETE ON
    alerts,
    notification_intents,
    alert_deliveries,
    processed_risk_events,
    alert_config,
    notification_cooldowns
TO :app_role;

-- Audit log: INSERT + SELECT only. No UPDATE, no DELETE - ever.
GRANT SELECT, INSERT ON alert_audit_log TO :app_role;
REVOKE UPDATE, DELETE, TRUNCATE ON alert_audit_log FROM :app_role;
REVOKE UPDATE, DELETE, TRUNCATE ON alert_audit_log FROM PUBLIC;

-- risk_predictions is M3-owned in production - M5's app role only ever
-- needs SELECT on it. (In this test/dev shim it's a local table; in
-- production this GRANT would be issued by M3, not M5.)
GRANT SELECT ON risk_predictions TO :app_role;
