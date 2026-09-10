"""Dev/test DB bootstrap: create M5's schema (+ the M3 test shim, if
requested) against ``settings.DATABASE_URL``.

Usage:
    python -m scripts.bootstrap_db            # M5 schema only
    python -m scripts.bootstrap_db --with-m3-shim   # + risk_predictions shim (test/dev only)

Does NOT apply migrations/0001_audit_role_and_permissions.sql - that
requires a superuser and creates a real login role with a password, so it
is run separately/explicitly (see migrations/README.md), not on every
bootstrap.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

from models.db_models import Base, async_engine
from models.external_models import external_metadata


async def main(with_m3_shim: bool) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        if with_m3_shim:
            shim_sql = (Path(__file__).parent.parent / "migrations" / "0000_test_only_m3_shim.sql").read_text()
            # Strip full-line comments before splitting on ';' - a naive
            # split breaks on semicolons that appear inside comment prose.
            cleaned_lines = [ln for ln in shim_sql.splitlines() if not ln.strip().startswith("--")]
            cleaned_sql = "\n".join(cleaned_lines)
            for statement in cleaned_sql.split(";"):
                statement = statement.strip()
                if statement:
                    await conn.execute(text(statement))
        await conn.run_sync(Base.metadata.create_all)
    print("M5 schema created.")


if __name__ == "__main__":
    asyncio.run(main(with_m3_shim="--with-m3-shim" in sys.argv))
