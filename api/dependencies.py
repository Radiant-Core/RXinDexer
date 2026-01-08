from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database.session import SessionLocal, AsyncSessionLocal
from api.auth import get_current_user, get_current_user_optional, User


def get_db():
    """Sync database dependency (for backward compatibility)."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def get_async_db() -> AsyncSession:
    """
    Async database dependency for non-blocking I/O.
    
    Use this for async endpoints to achieve 2-3x throughput improvement.
    
    Usage:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_async_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# Authentication dependencies
def get_current_authenticated_user(user: User = Depends(get_current_user)):
    """Dependency to require authentication."""
    return user


def get_optional_user(user: User = Depends(get_current_user_optional)):
    """Dependency for optional authentication."""
    return user
