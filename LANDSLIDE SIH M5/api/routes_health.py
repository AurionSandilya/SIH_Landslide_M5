"""Split health checks (architecture §44a): liveness vs readiness, so
orchestration can distinguish "process is dead, restart me" from
"a dependency is briefly unreachable"."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from models.db_models import get_async_session

router = APIRouter()


@router.get("/health/live", tags=["Health"])
async def health_live() -> JSONResponse:
    """Liveness probe - always 200 if the process can answer HTTP at all."""
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "live"})


@router.get("/health/ready", tags=["Health"])
async def health_ready(session: AsyncSession = Depends(get_async_session)) -> JSONResponse:
    """Readiness probe - checks DB connectivity. A DB outage means M5
    fails closed (architecture §40); readiness reflects that honestly
    rather than reporting healthy while unable to make alert decisions."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ready"})
