# How to Run the M5 Real-Time Data & Alerting Engine

## 1. Prerequisites

- Python 3.12+
- PostgreSQL 16 (a database instance the service can connect to)

## 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\Activate.ps1 on Windows
pip install -r requirements.txt
# for running the test suite:
pip install -r requirements-dev.txt
```

## 3. Configure environment variables

Create a `.env` file in the project root:

```dotenv
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/m5
M3_SERVICE_KEY=your-m3-service-key
M3_BASE_URL=http://localhost:8001
TWILIO_ACCOUNT_SID=your-twilio-sid
TWILIO_AUTH_TOKEN=your-twilio-token
TWILIO_FROM_NUMBER=+1...
FCM_SERVER_KEY=your-fcm-server-key
SCHEDULER_LOCK_KEY=987654321
LOG_LEVEL=INFO
```

`M3_SERVICE_KEY` is required in production. `ALLOW_INSECURE_NO_SERVICE_KEY=true`
disables internal-API auth for local dev only - never set it in production.

## 4. Create the schema

There is no Alembic setup yet (see `migrations/README.md` for why, and what
a production deployment should add). For now:

```bash
# M5's own tables (alerts, notification_intents, alert_deliveries,
# alert_audit_log, processed_risk_events, alert_config, notification_cooldowns)
python -m scripts.bootstrap_db

# Local/dev only, if you don't have a real M3 instance to point at: also
# creates a risk_predictions shim table so the pipeline is testable end-to-end.
python -m scripts.bootstrap_db --with-m3-shim
```

Then, to enforce audit-log immutability at the database role level
(architecture §34, Phase 9 - see `migrations/README.md`):

```bash
scripts/apply_audit_role_migration.sh <database> <app_role> <app_password>
```

Point `DATABASE_URL` at that role's credentials for the running service, not
the bootstrap superuser.

## 5. Run the test suite

```bash
# requires a running local Postgres; tests create/read/write real tables
# (TRUNCATEd between tests) - see tests/conftest.py
pytest
```

## 6. Start the service

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

On startup the service seeds `alert_config` defaults (if empty), attempts to
become the reconciliation-scheduler leader via a Postgres advisory lock
(`core/scheduler.py`), and starts the notification dispatcher background
loop. It exposes:

- `POST /internal/risk-event` - webhook entry point (requires
  `X-Internal-Service-Key`), body: `{"prediction_id": "<uuid>", "area_id": "<optional hint>"}`
- `POST /internal/lifecycle/acknowledge` - body: `{"alert_id": "<uuid>"}`
- `POST /internal/lifecycle/cancel` - body: `{"alert_id": "<uuid>", "reason": "..."}`
- `GET /health/live`, `GET /health/ready`

```bash
curl -s http://localhost:8000/health/live
```

## 7. Manual smoke test

```bash
# insert a risk_predictions row directly (stand-in for M1/M3 in a dev setup
# using the --with-m3-shim table), then trigger the webhook:
curl -X POST http://localhost:8000/internal/risk-event \
  -H "X-Internal-Service-Key: your-m3-service-key" \
  -H "Content-Type: application/json" \
  -d '{"prediction_id": "<uuid-from-risk_predictions>"}'
```

## 8. Stopping the service

Ctrl-C. The lifespan shutdown handler cancels the dispatcher task, releases
the scheduler advisory lock, closes the M3 HTTP client, and disposes the DB
engine.

## 9. Known remaining gaps

See the compliance report delivered alongside this fix for the full list -
in short: no Alembic migration tooling yet (raw SQL + `create_all` for now),
provider integrations are tested with mocks only (no real Twilio/FCM
credentials available in this environment), and the `adapters/`/`ingestion/`
raw-observation-source modules are outside the scope of `M5 Architecture.md`
and were left largely as found (one `NameError` typo fixed).
