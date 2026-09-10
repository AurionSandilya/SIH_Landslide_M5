# Repository for generic observations (placeholder)

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

# NOTE: The concrete observation tables (rainfall, weather, earthquake, sar)
# are not defined yet in the ORM.  This repository provides a generic interface
# that can be extended once the models are added.

class ObservationRepository:
    """Placeholder repository for observation inserts.

    The architecture expects adapters to persist normalized observations.  When
    the concrete ORM models are added, replace the ``table`` argument with the
    appropriate model class.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert_observation(self, table, data: dict):
        """Insert a single observation record into the given ``table``.

        ``table`` should be a SQLAlchemy model class.  ``data`` is a mapping of
        column names to values.
        """
        stmt = insert(table).values(**data).returning(table)
        result = await self.session.execute(stmt)
        return result.fetchone()

    async def bulk_insert(self, table, rows: list[dict]):
        """Bulk insert a list of observation dictionaries.
        Returns the list of inserted rows.
        """
        stmt = insert(table).values(rows).returning(table)
        result = await self.session.execute(stmt)
        return result.fetchall()
