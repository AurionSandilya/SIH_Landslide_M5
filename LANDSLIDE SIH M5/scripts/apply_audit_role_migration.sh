#!/usr/bin/env bash
# Creates (if missing) the restricted M5 application role and applies
# migrations/0001_audit_role_and_permissions.sql against it.
#
# Usage: scripts/apply_audit_role_migration.sh <database> <app_role> <app_password>
set -euo pipefail
DB="${1:?database name required}"
APP_ROLE="${2:?app role name required}"
APP_PASSWORD="${3:?app password required}"

EXISTS=$(psql -d "$DB" -tAc "SELECT 1 FROM pg_roles WHERE rolname = '${APP_ROLE}'")
if [ "$EXISTS" != "1" ]; then
    psql -d "$DB" -c "CREATE ROLE ${APP_ROLE} LOGIN PASSWORD '${APP_PASSWORD}'"
fi

psql -d "$DB" -v app_role="${APP_ROLE}" -f "$(dirname "$0")/../migrations/0001_audit_role_and_permissions.sql"
