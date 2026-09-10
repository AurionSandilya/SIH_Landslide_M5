# Migrations

M5's own tables (`alerts`, `notification_intents`, `alert_deliveries`,
`alert_audit_log`, `processed_risk_events`, `alert_config`,
`notification_cooldowns`) are defined once, in `models/db_models.py`, and
created via `scripts/bootstrap_db.py` (`Base.metadata.create_all`) for
dev/test. **A real production deployment should replace this with Alembic
autogeneration from the same `Base.metadata`** so schema changes are
versioned - that migration-tooling setup is flagged as a remaining
limitation in the final compliance report, not silently pretended-away.

Two things can't be expressed via SQLAlchemy's `create_all` and live here
as hand-written SQL instead:

- `0001_audit_role_and_permissions.sql` - creates the restricted `m5_app`
  DB role and revokes `UPDATE`/`DELETE` on `alert_audit_log` (architecture
  §34, Phase 9). This is what makes audit-log immutability a database
  guarantee, not just an application convention.
- `0000_test_only_m3_shim.sql` - creates a `risk_predictions` table
  matching M3's schema (architecture §7) so the full pipeline can be
  exercised end-to-end against a single local Postgres instance without a
  running M3 service. **This file must never be applied against a real
  deployment** - in production `risk_predictions` is M3's table, created
  and migrated by M3, not by M5.

Apply order: `0000` (test only) -> `Base.metadata.create_all` -> `0001`.
