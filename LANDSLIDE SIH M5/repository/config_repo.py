"""Repository for ``AlertConfig`` key/value settings (architecture §19a).

Every config value that affects an alert decision lives here, not as a
Python constant - and every change is audited as ``CONFIG_CHANGED``
(invariant #18). ``core.constants.DEFAULT_CONFIG`` supplies bootstrap
defaults only; once a row exists in this table it always wins.
"""

from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import DEFAULT_CONFIG
from core.exceptions import ConfigError
from models.db_models import AlertConfig


class ConfigRepository:
    """CRUD for alert configuration entries, with an in-process cache.

    The cache is refreshed on every ``set``/``bootstrap_defaults`` call and
    can be force-refreshed via ``refresh()`` (e.g. on a reconciliation
    tick), which is the "short poll interval" bootstrap described in §19a
    without needing a pub/sub layer.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self._cache: dict[str, str] = {}
        self._loaded = False

    async def get_raw(self, key: str) -> Optional[AlertConfig]:
        result = await self.session.execute(select(AlertConfig).where(AlertConfig.config_key == key))
        return result.scalar_one_or_none()

    async def refresh(self) -> None:
        result = await self.session.execute(select(AlertConfig))
        self._cache = {row.config_key: row.config_value for row in result.scalars().all()}
        self._loaded = True

    async def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if not self._loaded:
            await self.refresh()
        if key in self._cache:
            return self._cache[key]
        if key in DEFAULT_CONFIG:
            return DEFAULT_CONFIG[key]
        return default

    async def get_required(self, key: str) -> str:
        value = await self.get(key)
        if value is None:
            raise ConfigError(f"Missing required config key: {key}")
        return value

    async def get_int(self, key: str, default: Optional[int] = None) -> int:
        raw = await self.get(key)
        if raw is None:
            if default is None:
                raise ConfigError(f"Missing required int config key: {key}")
            return default
        return int(raw)

    async def get_float(self, key: str, default: Optional[float] = None) -> float:
        raw = await self.get(key)
        if raw is None:
            if default is None:
                raise ConfigError(f"Missing required float config key: {key}")
            return default
        return float(raw)

    async def get_bool(self, key: str, default: bool = False) -> bool:
        raw = await self.get(key)
        if raw is None:
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")

    async def set(self, key: str, value: str, updated_by: str) -> AlertConfig:
        """Upsert a config value. Callers that need the CONFIG_CHANGED audit
        trail (architecture §19a) should go through
        ``alerting.config_service.set_config`` instead of calling this
        directly, so the audit write and the value change land in the same
        transaction."""
        stmt = (
            pg_insert(AlertConfig)
            .values(config_key=key, config_value=value, updated_by=updated_by)
            .on_conflict_do_update(
                index_elements=[AlertConfig.config_key],
                set_=dict(config_value=value, updated_by=updated_by),
            )
            .returning(AlertConfig)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one()
        self._cache[key] = value
        self._loaded = True
        return row

    async def delete(self, key: str) -> None:
        await self.session.execute(delete(AlertConfig).where(AlertConfig.config_key == key))
        self._cache.pop(key, None)

    async def bootstrap_defaults(self, updated_by: str = "system:bootstrap") -> int:
        """Seed any DEFAULT_CONFIG keys not already present in the DB.
        Existing rows are never overwritten - the DB table always wins once
        populated. Returns the number of keys inserted."""
        await self.refresh()
        inserted = 0
        for key, value in DEFAULT_CONFIG.items():
            if key in self._cache:
                continue
            stmt = (
                pg_insert(AlertConfig)
                .values(config_key=key, config_value=value, updated_by=updated_by)
                .on_conflict_do_nothing(index_elements=[AlertConfig.config_key])
            )
            await self.session.execute(stmt)
            inserted += 1
        await self.refresh()
        return inserted
