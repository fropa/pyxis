from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models.tenant import Tenant

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_current_tenant(
    api_key: str = Security(api_key_header),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    result = await db.execute(select(Tenant).where(Tenant.api_key == api_key, Tenant.is_active == True))
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    return tenant
